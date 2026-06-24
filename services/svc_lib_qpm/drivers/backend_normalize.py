# Qiskit BackendV2 -> qhw normalization for the device-introspection facet.
#
# Used by the QDMI driver: QDMI-on-IQM (iqm.qdmi.qiskit.IQMBackend) is a
# vendor-neutral Qiskit BackendV2 with no raw IQM data to reuse, so its topology
# is read straight off the Target and normalized here. (The QRMI driver does
# NOT use this module: QRMI's target() returns raw IQM data, which it feeds to
# qhw-iqm directly — the same normalizer the native svc_iqm_qpm path uses.)
#
# The output is the same provider-neutral qhw-data schema the native IQM path
# emits, so a consumer sees one device/coupling record regardless of which
# library served the call.
#
# The module is split into two layers so normalization is testable without
# hardware or credentials:
#   - extract_topology(backend): the only part that touches a live Qiskit
#     BackendV2 (duck-typed: num_qubits, coupling_map, target);
#   - to_device_record / to_coupling_record: pure functions over the extracted
#     dict, building qhw-device-v1 / qhw-coupling-v1 with the qhw_data builders.
#
# qhw_data is imported lazily inside the record builders (as the native IQM
# path defers qhw-iqm) so the drivers import and the Frontend routes even where
# qhw-data is not installed; only a real introspection call needs it.

# Where in the Qiskit backend each piece of the graph was read from --
# recorded in the qhw coupling record's `source` for provenance.
_COUPLING_MAP_SOURCE = "backend.coupling_map"
_OPERATION_LOCI_SOURCE = "backend.target.operations.*.loci"


def extract_topology(backend):
	"""Read a provider-neutral topology dict off a Qiskit BackendV2.

	Returns a dict with:
	  num_qubits: int
	  qubits:     list[str]  -- qubit ids ("0".."n-1", BackendV2 indices)
	  edges:      list[[str, str]]  -- undirected, de-duplicated coupling edges
	  operations: dict[str, list[list[str]]]  -- gate name -> supported loci

	Only this function depends on the Qiskit backend object; everything else
	in this module is pure and can be exercised with a synthetic topology dict.
	"""
	num_qubits = int(getattr(backend, "num_qubits", 0) or 0)
	qubits = [str(q) for q in range(num_qubits)]
	operations = _operations(backend)
	edges = _coupling_edges(backend, operations)
	return {
		"num_qubits": num_qubits,
		"qubits": qubits,
		"edges": edges,
		"operations": operations,
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
		.metadata({"source": "qdmi", "via": "iqm.qdmi.qiskit"})
	)
	if include_raw:
		builder.raw_payload(topo, format="qdmi-qiskit-topology")
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
		sources.append(_COUPLING_MAP_SOURCE)
	builder = (
		new_coupling(provider, device_id, num_qubits=len(qubits),
				directed=False)
		.nodes(qubits)
		.coupling(edges, directed=False, nodes=qubits, source=sources)
		.metadata({"source": "qdmi", "via": "iqm.qdmi.qiskit"})
	)
	for name, loci in sorted(operations.items()):
		arity = max((len(locus) for locus in loci), default=0)
		builder.operation(name, arity, supported_loci=loci)
	if include_raw:
		builder.raw_payload(topo, format="qdmi-qiskit-topology")
	return builder.build(validate_schema=validate)


# --- backend extraction helpers (Qiskit BackendV2, duck-typed) -----------

def _operations(backend):
	target = getattr(backend, "target", None)
	if target is None:
		return {}
	try:
		names = list(target.operation_names)
	except Exception:
		return {}
	operations = {}
	for name in names:
		operations[name] = _operation_loci(target, name)
	return operations


def _operation_loci(target, name):
	# target[name] maps qargs tuples -> InstructionProperties; qargs is None
	# for global/variadic instructions (no specific locus).
	try:
		qargs_map = target[name]
	except Exception:
		return []
	loci = []
	seen = set()
	for qargs in (qargs_map or {}):
		if not qargs:
			continue
		locus = [str(q) for q in qargs]
		key = tuple(locus)
		if key not in seen:
			seen.add(key)
			loci.append(locus)
	return sorted(loci, key=_locus_key)


def _coupling_edges(backend, operations):
	# Prefer the backend's coupling map; fall back to the two-qubit operation
	# loci (mirrors how qhw-iqm derives connectivity when none is declared).
	pairs = []
	cmap = getattr(backend, "coupling_map", None)
	if cmap is not None:
		try:
			raw = cmap.get_edges()
		except AttributeError:
			raw = list(cmap)
		for edge in raw:
			edge = list(edge)
			if len(edge) == 2:
				pairs.append((str(edge[0]), str(edge[1])))
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
