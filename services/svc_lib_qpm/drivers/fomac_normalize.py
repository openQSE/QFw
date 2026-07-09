# FoMaC Device -> qhw normalization for the device-introspection facet.
#
# QDMI is vendor-neutral: its FoMaC Device API (mqt.core.fomac) already exposes
# the real qubit names, connectivity, gate loci, and calibration (T1/T2, gate
# fidelity) through the QDMI query interface. The QDMI driver reads that
# directly -- no Qiskit Target, no raw vendor JSON -- and this builds the same
# provider-neutral qhw-data records the native / QRMI paths emit, so a consumer
# sees one device/coupling record regardless of which library served the call.
#
# Split so normalization is testable without hardware or credentials:
#   - extract_topology(device): the only part that touches a live FoMaC Device
#     (duck-typed: regular_sites(), operations(), coupling_map());
#   - to_device_record / to_coupling_record: pure functions over the extracted
#     dict, building qhw-device-v1 / qhw-coupling-v1 with the qhw_data builders.
#
# qhw_data is imported lazily inside the record builders (as the native IQM path
# defers qhw-iqm) so the driver imports and the Frontend routes even where
# qhw-data is not installed; only a real introspection call needs it.

_COUPLING_SOURCE = "qdmi.fomac.coupling_map"


def extract_topology(device):
	"""Read a provider-neutral topology dict off a FoMaC Device.

	Returns a dict with:
	  num_qubits: int
	  qubits:     list[str]  -- real device labels (e.g. "QB1"), via QDMI
	  edges:      list[[str, str]]  -- undirected, de-duplicated coupling edges
	  operations: dict[str, list[list[str]]]  -- gate name -> supported loci

	Only this function depends on the FoMaC Device object; everything else in
	this module is pure and can be exercised with a synthetic topology dict.
	"""
	sites = list(device.regular_sites() or [])
	qubits = [_site_label(site) for site in sites]
	operations = _operations(device)
	edges = _coupling_edges(device, operations)
	return {
		"num_qubits": len(qubits),
		"qubits": qubits,
		"edges": edges,
		"operations": operations,
	}


def extract_calibration(device):
	"""Read provider-neutral calibration metrics off a FoMaC Device.

	Returns a dict with:
	  qubits:        list[str]  -- real device labels (e.g. "QB1")
	  qubit_metrics: list[dict] -- {qubit, t1?, t2?} per qubit that reports one
	  gate_metrics:  list[dict] -- {operation, locus, fidelity?, duration?} per
	                               operation locus that reports one
	  duration_unit: str | None -- unit for the duration values

	QDMI exposes coherence (QDMI_SITE_PROPERTY_T1/T2) per site and fidelity /
	duration per operation locus; this reads them vendor-neutrally. Like
	extract_topology, this is the only calibration function that touches the
	live FoMaC Device -- to_calibration_record is pure over the returned dict.
	"""
	sites = list(device.regular_sites() or [])
	qubit_metrics = []
	for site in sites:
		entry = {"qubit": _site_label(site)}
		t1 = _site_metric(site, "t1")
		if t1 is not None:
			entry["t1"] = t1
		t2 = _site_metric(site, "t2")
		if t2 is not None:
			entry["t2"] = t2
		qubit_metrics.append(entry)
	return {
		"qubits": [_site_label(site) for site in sites],
		"qubit_metrics": qubit_metrics,
		"gate_metrics": _operation_metrics(device),
		"duration_unit": _device_duration_unit(device),
	}


def to_device_record(topo, provider, device_id, include_raw=False,
		validate=True):
	"""Build a `qhw-device-v1` record from an extracted topology dict."""
	from qhw_data import new_device
	qubits = topo.get("qubits") or []
	builder = (
		new_device(provider, device_id, num_qubits=len(qubits))
		.device(device_id, num_qubits=len(qubits))
		.qubits(qubits)
		.metadata({"source": "qdmi", "via": "mqt.core.fomac"})
	)
	if include_raw:
		builder.raw_payload(topo, format="qdmi-fomac-topology")
	return builder.build(validate_schema=validate)


def to_coupling_record(topo, provider, device_id, include_raw=False,
		validate=True):
	"""Build a `qhw-coupling-v1` record from an extracted topology dict."""
	from qhw_data import new_coupling
	qubits = topo.get("qubits") or []
	edges = topo.get("edges") or []
	operations = topo.get("operations") or {}

	sources = []
	if edges:
		sources.append(_COUPLING_SOURCE)
	builder = (
		new_coupling(provider, device_id, num_qubits=len(qubits),
				directed=False)
		.nodes(qubits)
		.coupling(edges, directed=False, nodes=qubits, source=sources)
		.metadata({"source": "qdmi", "via": "mqt.core.fomac"})
	)
	for name, loci in sorted(operations.items()):
		arity = max((len(locus) for locus in loci), default=0)
		builder.operation(name, arity, supported_loci=loci)
	if include_raw:
		builder.raw_payload(topo, format="qdmi-fomac-topology")
	return builder.build(validate_schema=validate)


def to_calibration_record(cal, provider, device_id, include_raw=False,
		validate=True):
	"""Build a `qhw-calibration-v1` record from an extracted calibration dict.

	QDMI (via FoMaC) reports the current per-qubit coherence and per-gate
	fidelity rather than IQM's calibration/quality observation sets, so the
	record carries counts + summaries in the core fields and the measured
	values under the `qdmi.fomac.v1` extension. The output still validates as
	`qhw-calibration-v1`, so a consumer sees one calibration shape regardless of
	which library served the call (QRMI populates the IQM observation identity;
	QDMI populates the measured metrics).
	"""
	from qhw_data import new_calibration
	qubits = cal.get("qubits") or []
	qubit_metrics = cal.get("qubit_metrics") or []
	gate_metrics = cal.get("gate_metrics") or []
	coherence_qubits = sum(
		1 for m in qubit_metrics if "t1" in m or "t2" in m)
	operations_measured = sorted(
		{m["operation"] for m in gate_metrics if m.get("operation")})
	builder = (
		new_calibration(provider, device_id, num_qubits=len(qubits) or None)
		.calibration(
			observation_count=coherence_qubits,
			quality_metric_count=len(gate_metrics))
		.summaries({
			"source": "qdmi",
			"via": "mqt.core.fomac",
			"qubits_with_coherence": coherence_qubits,
			"gate_loci_measured": len(gate_metrics),
			"operations_measured": operations_measured,
			"duration_unit": cal.get("duration_unit"),
		})
		.extension("qdmi.fomac.v1", {
			"qubit_metrics": qubit_metrics,
			"gate_metrics": gate_metrics,
			"duration_unit": cal.get("duration_unit"),
		})
	)
	if include_raw:
		builder.raw_payload(cal, format="qdmi-fomac-calibration")
	return builder.build(validate_schema=validate)


def to_result_record(counts, shots, provider, device_id, job_id=None,
		status="completed", validate=True):
	"""Build a `qhw-result-v1` record from a QDMI counts histogram.

	QDMI (via FoMaC) returns a plain {bitstring: count} histogram from
	`Job.get_counts()`; normalize it to the same `qhw-result-v1` the QRMI/native
	path produces via qhw-iqm, so a consumer sees one result shape regardless of
	which library executed the circuit. Pure over the counts dict.
	"""
	from qhw_data import new_result
	counts = counts or {}
	resolved_shots = int(shots) if shots is not None else (
		sum(counts.values()) or None)
	builder = (
		new_result(provider, device_id, job_status=status)
		.job(id=job_id, status=status)
		.result(
			shots=resolved_shots,
			num_circuits=1,
			counts=counts,
			success=(status == "completed"))
		.metadata({"source": "qdmi", "via": "mqt.core.fomac"})
	)
	return builder.build(validate_schema=validate)


# --- FoMaC Device extraction helpers (duck-typed) ------------------------

def _site_label(site):
	# Prefer the device's real label (QDMI_SITE_PROPERTY_NAME, e.g. "QB1"); fall
	# back to the site index when a device leaves the name unset.
	name = None
	try:
		name = site.name()
	except Exception:
		name = None
	if name:
		return str(name)
	try:
		return str(site.index())
	except Exception:
		return ""


def _operations(device):
	try:
		ops = list(device.operations() or [])
	except Exception:
		return {}
	operations = {}
	for op in ops:
		try:
			name = op.name()
		except Exception:
			continue
		operations[name] = _operation_loci(op)
	return operations


def _operation_loci(op):
	# A FoMaC Operation exposes the loci it supports: site_pairs() for local
	# 2-qubit operations, otherwise sites() (each site is a 1-qubit locus).
	loci = []
	seen = set()
	pairs = None
	try:
		pairs = op.site_pairs()
	except Exception:
		pairs = None
	if pairs:
		for pair in pairs:
			locus = [_site_label(pair[0]), _site_label(pair[1])]
			key = tuple(locus)
			if key not in seen:
				seen.add(key)
				loci.append(locus)
		return sorted(loci, key=_locus_key)
	sites = None
	try:
		sites = op.sites()
	except Exception:
		sites = None
	for site in (sites or []):
		locus = [_site_label(site)]
		key = tuple(locus)
		if key not in seen:
			seen.add(key)
			loci.append(locus)
	return sorted(loci, key=_locus_key)


def _site_metric(site, attr):
	# Read a per-site coherence metric (t1/t2); None when absent/unsupported.
	getter = getattr(site, attr, None)
	if getter is None:
		return None
	try:
		return getter()
	except Exception:
		return None


def _operation_metrics(device):
	# Per-operation-locus fidelity/duration. Mirrors _operations() but keeps the
	# Site objects so the metric accessors can be queried per locus. Only loci
	# that actually report a metric are recorded.
	try:
		ops = list(device.operations() or [])
	except Exception:
		return []
	metrics = []
	for op in ops:
		try:
			name = op.name()
		except Exception:
			continue
		for locus_sites in _operation_loci_sites(op):
			entry = {
				"operation": name,
				"locus": [_site_label(site) for site in locus_sites],
			}
			fidelity = _op_metric(op, "fidelity", locus_sites)
			if fidelity is not None:
				entry["fidelity"] = fidelity
			duration = _op_metric(op, "duration", locus_sites)
			if duration is not None:
				entry["duration"] = duration
			if "fidelity" in entry or "duration" in entry:
				metrics.append(entry)
	return sorted(metrics,
			key=lambda m: (m["operation"], _locus_key(m["locus"])))


def _operation_loci_sites(op):
	# The loci an operation supports, as lists of FoMaC Site objects (parallel
	# to _operation_loci, which returns labels). An operation with neither
	# site_pairs() nor sites() is treated as a single global locus ([]).
	pairs = None
	try:
		pairs = op.site_pairs()
	except Exception:
		pairs = None
	if pairs:
		return [[pair[0], pair[1]] for pair in pairs]
	sites = None
	try:
		sites = op.sites()
	except Exception:
		sites = None
	if sites:
		return [[site] for site in sites]
	return [[]]


def _op_metric(op, attr, locus_sites):
	# Read a per-locus operation metric (fidelity/duration); None when absent.
	getter = getattr(op, attr, None)
	if getter is None:
		return None
	try:
		if locus_sites:
			return getter(sites=locus_sites)
		return getter()
	except Exception:
		return None


def _device_duration_unit(device):
	getter = getattr(device, "duration_unit", None)
	if getter is None:
		return None
	try:
		return getter()
	except Exception:
		return None


def _coupling_edges(device, operations):
	# Prefer the device coupling map; fall back to the two-qubit operation loci
	# (mirrors how qhw-iqm derives connectivity when none is declared).
	pairs = []
	cmap = None
	try:
		cmap = device.coupling_map()
	except Exception:
		cmap = None
	for edge in (cmap or []):
		pairs.append((_site_label(edge[0]), _site_label(edge[1])))
	if not pairs:
		for loci in operations.values():
			for locus in loci:
				if len(locus) == 2:
					pairs.append((locus[0], locus[1]))
	return _undirected_unique(pairs)


def _undirected_unique(pairs):
	seen = set()
	unique = []
	for a, b in pairs:
		key = tuple(sorted((a, b), key=_qubit_key))
		if key not in seen:
			seen.add(key)
			unique.append([a, b])
	return sorted(unique, key=lambda edge: (_qubit_key(edge[0]),
			_qubit_key(edge[1])))


def _qubit_key(value):
	# Natural sort so "10" orders after "2" rather than lexicographically.
	text = str(value)
	if text.isdigit():
		return (0, int(text), "")
	return (1, 0, text)


def _locus_key(locus):
	return tuple(_qubit_key(q) for q in locus)
