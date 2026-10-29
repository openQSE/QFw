"""Read authoritative qhw records for SDK-specific target helpers."""

from collections.abc import Mapping, Sequence


DURATION_UNITS = {
	"s": 1.0,
	"ms": 1e-3,
	"us": 1e-6,
	"µs": 1e-6,
	"ns": 1e-9,
	"ps": 1e-12,
}


def nodes(device, coupling):
	"""Return qhw node identifiers in their authoritative order."""
	for value in (
		_nested(coupling, "coupling", "nodes"),
		coupling.get("nodes") if isinstance(coupling, Mapping) else None,
		device.get("qubits") if isinstance(device, Mapping) else None,
	):
		if not _sequence(value):
			continue
		labels = tuple(_node_label(node) for node in value)
		if len(set(labels)) != len(labels):
			raise ValueError("qhw node identifiers must be unique")
		return labels
	raise ValueError("qhw records contain no node identifiers")


def edges(coupling, index_by_node):
	"""Return explicit indexed connectivity without inferring any edges."""
	graph = coupling.get("coupling") if isinstance(coupling, Mapping) else None
	if not isinstance(graph, Mapping):
		graph = coupling if isinstance(coupling, Mapping) else {}
	raw = graph.get("edges") or graph.get("couplings") or ()
	if not _sequence(raw) or not raw:
		raise ValueError("qhw coupling record contains no explicit edges")
	result = []
	for edge in raw:
		if not _sequence(edge) or len(edge) != 2:
			raise ValueError("qhw coupling record contains an invalid edge")
		try:
			indexed = tuple(index_by_node[_node_label(node)] for node in edge)
		except KeyError as exc:
			raise ValueError("qhw edge references an unknown node") from exc
		if indexed not in result:
			result.append(indexed)
	if not directed(coupling):
		for edge in tuple(result):
			reverse = tuple(reversed(edge))
			if reverse not in result:
				result.append(reverse)
	return tuple(result)


def directed(coupling):
	graph = coupling.get("coupling") if isinstance(coupling, Mapping) else None
	if isinstance(graph, Mapping):
		return bool(graph.get("directed", False))
	return bool(coupling.get("directed", False)) if isinstance(
		coupling, Mapping) else False


def operations(device, coupling):
	"""Return structured qhw operations without reducing their contents."""
	for record in (coupling, device):
		if not isinstance(record, Mapping):
			continue
		value = record.get("operations")
		if _sequence(value):
			for operation in value:
				if not isinstance(operation, Mapping):
					raise ValueError("qhw operation must be a mapping")
			return tuple(value)
	return ()


def calibration_metrics(calibration):
	"""Return per-operation and per-site metrics normalized to seconds."""
	gates = {}
	qubits = {}
	if not isinstance(calibration, Mapping):
		return gates, qubits
	_direct_metrics(calibration, gates)
	extensions = calibration.get("extensions") or {}
	if not isinstance(extensions, Mapping):
		return gates, qubits
	fomac = extensions.get("qdmi.fomac.v1")
	if isinstance(fomac, Mapping):
		_fomac_metrics(fomac, gates, qubits)
	iqm = extensions.get("iqm.v1")
	if isinstance(iqm, Mapping):
		_iqm_metrics(iqm, gates, qubits)
	return gates, qubits


def operation_loci(operation, index_by_node):
	name = operation.get("name")
	loci = operation.get("supported_loci")
	if not _sequence(loci):
		raise ValueError(f"qhw operation {name!r} has no supported loci")
	result = []
	for locus in loci:
		if not _sequence(locus):
			raise ValueError(f"qhw operation {name!r} has an invalid locus")
		try:
			indexed = tuple(index_by_node[_node_label(node)] for node in locus)
		except KeyError as exc:
			raise ValueError(
				f"qhw operation {name!r} references an unknown node") from exc
		result.append(indexed)
	return tuple(result)


def locus_metric(metrics, operation_name, nodes, symmetric=False):
	metric = dict(metrics.get((str(operation_name), tuple(nodes))) or {})
	if symmetric:
		reverse = metrics.get((str(operation_name), tuple(reversed(nodes)))) or {}
		for key, value in reverse.items():
			metric.setdefault(key, value)
	return metric


def _direct_metrics(calibration, gates):
	properties = calibration.get("properties_by_locus") or {}
	if not isinstance(properties, Mapping):
		return
	for key, values in properties.items():
		if not isinstance(values, Mapping):
			continue
		parts = tuple(str(key).split(","))
		operation = values.get("operation")
		if operation:
			gates[(str(operation), parts)] = dict(values)


def _fomac_metrics(fomac, gates, qubits):
	scale = _duration_scale(fomac.get("duration_unit"))
	for entry in fomac.get("gate_metrics") or ():
		if not isinstance(entry, Mapping) or not entry.get("operation"):
			continue
		key = (str(entry["operation"]), tuple(
			str(node) for node in entry.get("locus") or ()))
		metric = gates.setdefault(key, {})
		if _number(entry.get("fidelity")):
			metric["fidelity"] = float(entry["fidelity"])
			metric["error"] = _error(entry["fidelity"])
		if _number(entry.get("duration")) and scale is not None:
			metric["duration"] = float(entry["duration"]) * scale
	for entry in fomac.get("qubit_metrics") or ():
		if not isinstance(entry, Mapping) or entry.get("qubit") is None:
			continue
		metric = qubits.setdefault(str(entry["qubit"]), {})
		for name in ("t1", "t2"):
			if _number(entry.get(name)) and scale is not None:
				metric[name] = float(entry[name]) * scale


def _iqm_metrics(iqm, gates, qubits):
	for name, value, unit in _observations(iqm.get("quality_metric_set")):
		parts = name.split(":", 1)[0].split(".")
		if (len(parts) == 6 and parts[0] == "metrics" and
				parts[1] in ("rb", "irb", "ssro") and
				parts[5] == "fidelity"):
			metric = gates.setdefault(
				(parts[2], tuple(parts[4].split("__"))), {})
			metric["fidelity"] = float(value)
			metric["error"] = _error(value)
		elif (len(parts) == 4 and parts[0] == "characterization" and
				parts[3] in ("t1_time", "t2_time")):
			seconds = _seconds(value, unit)
			if seconds is not None:
				qubits.setdefault(parts[2], {})[parts[3][:2]] = seconds
	for name, value, unit in _observations(iqm.get("calibration_set")):
		parts = name.split(".")
		if len(parts) == 5 and parts[0] == "gates" and parts[4] == "duration":
			seconds = _seconds(value, unit)
			if seconds is not None:
				gates.setdefault(
					(parts[1], tuple(parts[3].split("__"))),
					{})["duration"] = seconds


def _observations(value):
	if not isinstance(value, Mapping):
		return
	for entry in value.get("observations") or ():
		if (not isinstance(entry, Mapping) or entry.get("invalid") or
				not isinstance(entry.get("dut_field"), str) or
				not _number(entry.get("value"))):
			continue
		yield entry["dut_field"], entry["value"], entry.get("unit")


def _nested(record, outer, inner):
	if not isinstance(record, Mapping):
		return None
	value = record.get(outer)
	return value.get(inner) if isinstance(value, Mapping) else None


def _node_label(node):
	if isinstance(node, Mapping):
		node = node.get("id")
	if node is None:
		raise ValueError("qhw node has no identifier")
	return str(node)


def _sequence(value):
	return isinstance(value, Sequence) and not isinstance(
		value, (str, bytes, bytearray))


def _duration_scale(unit):
	return DURATION_UNITS.get(str(unit or "s").strip().lower())


def _seconds(value, unit):
	scale = _duration_scale(unit)
	return float(value) * scale if scale is not None else None


def _error(fidelity):
	return min(max(1.0 - float(fidelity), 0.0), 1.0)


def _number(value):
	return isinstance(value, (int, float)) and not isinstance(value, bool)
