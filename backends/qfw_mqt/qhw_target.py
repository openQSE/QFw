"""Transitional qhw-to-MQT compiler-target conversion.

Remove this helper when MQT Core consumes standardized qhw records directly.
The module has no QFw service, reservation, credential, or execution imports.
"""

from collections.abc import Mapping, Sequence
from importlib import metadata

from qfw_target_data import (
	calibration_metrics,
	edges,
	locus_metric,
	nodes,
	operation_loci,
	operations,
)


SUPPORTED_MQT_CORE_VERSIONS = frozenset({"4.1.1"})
PICOSECONDS_PER_SECOND = 1_000_000_000_000

CANONICAL_OPERATIONS = {
	"prx": "r",
	"reset_wait": "reset",
	"cnot": "cx",
	"ccnot": "ccx",
	"toffoli": "ccx",
	"i": "id",
	"phaseshift": "p",
	"cphaseshift": "cp",
	"si": "sdg",
	"ti": "tdg",
	"v": "sx",
	"vi": "sxdg",
	"xx": "rxx",
	"yy": "ryy",
	"zz": "rzz",
}


def qhw_to_mqt_target(qhw_device, qhw_coupling, qhw_calibration=None):
	"""Build an MQT ``CompilerTarget`` from authoritative qhw mappings."""
	version = metadata.version("mqt-core")
	if version not in SUPPORTED_MQT_CORE_VERSIONS:
		raise RuntimeError(f"unsupported MQT Core version {version}")
	from mqt.core.mlir import CompilerTarget

	node_ids = nodes(qhw_device, qhw_coupling)
	index_by_node = {node: index for index, node in enumerate(node_ids)}
	connectivity = CompilerTarget.Connectivity(
		edges(qhw_coupling, index_by_node))
	qhw_operations = operations(qhw_device, qhw_coupling)
	if not qhw_operations:
		raise ValueError("qhw records contain no operations")
	gate_metrics, qubit_metrics = calibration_metrics(qhw_calibration)
	capabilities = [
		_operation_capability(
			CompilerTarget, operation, node_ids, index_by_node, gate_metrics)
		for operation in qhw_operations
	]
	sites = [
		CompilerTarget.Site(
			index,
			name=node,
			t1=_picoseconds((qubit_metrics.get(node) or {}).get("t1")),
			t2=_picoseconds((qubit_metrics.get(node) or {}).get("t2")),
		)
		for index, node in enumerate(node_ids)
	]
	return CompilerTarget(
		_device_id(qhw_device),
		sites,
		connectivity=connectivity,
		native_operations=CompilerTarget.NativeOperations(capabilities),
		duration_unit=CompilerTarget.DurationUnit("ps", 1e-12),
	)


def _operation_capability(
		compiler_target, operation, node_ids, index_by_node, metrics):
	reported_name = str(operation.get("name") or "")
	if not reported_name:
		raise ValueError("qhw operation has no name")
	try:
		arity = int(operation.get("arity"))
	except (TypeError, ValueError) as exc:
		raise ValueError(
			f"qhw operation {reported_name!r} has no valid arity") from exc
	parameters = operation.get("parameters") or ()
	if not _sequence(parameters):
		raise ValueError(
			f"qhw operation {reported_name!r} parameters must be a sequence")
	loci = operation_loci(operation, index_by_node)
	site_tuples = []
	for locus in loci:
		labels = tuple(node_ids[index] for index in locus)
		values = _operation_metric(operation, labels)
		values.update(locus_metric(metrics, reported_name, labels))
		site_tuples.append(compiler_target.SiteTuple(
			locus,
			duration=_picoseconds(values.get("duration")),
			fidelity=_fidelity(values),
		))
	return compiler_target.OperationCapability(
		reported_name,
		arity,
		len(parameters),
		site_tuples,
		canonical_name=CANONICAL_OPERATIONS.get(reported_name, reported_name),
		parameter_bounds=_parameter_bounds(parameters),
		fixed_parameters=_fixed_parameters(parameters),
	)


def _parameter_bounds(parameters):
	result = []
	for parameter in parameters:
		bounds = parameter.get("bounds") if isinstance(parameter, Mapping) else None
		if bounds is None:
			result.append(None)
		elif _sequence(bounds) and len(bounds) == 2:
			result.append((float(bounds[0]), float(bounds[1])))
		else:
			raise ValueError("qhw operation parameter has invalid bounds")
	return tuple(result)


def _fixed_parameters(parameters):
	result = []
	for parameter in parameters:
		value = None
		if isinstance(parameter, Mapping):
			value = parameter.get("fixed", parameter.get("value"))
		result.append(float(value) if value is not None else None)
	return tuple(result)


def _operation_metric(operation, labels):
	properties = operation.get("properties_by_locus") or {}
	if not isinstance(properties, Mapping):
		return {}
	value = properties.get(",".join(labels)) or properties.get(tuple(labels))
	return dict(value) if isinstance(value, Mapping) else {}


def _fidelity(values):
	if values.get("fidelity") is not None:
		return float(values["fidelity"])
	if values.get("error") is not None:
		return 1.0 - float(values["error"])
	return None


def _picoseconds(seconds):
	if seconds is None:
		return None
	value = float(seconds)
	if value < 0:
		raise ValueError("qhw duration and coherence values must be nonnegative")
	return round(value * PICOSECONDS_PER_SECOND)


def _device_id(device):
	if isinstance(device, Mapping):
		identity = device.get("device")
		if isinstance(identity, Mapping) and identity.get("id") is not None:
			return str(identity["id"])
		if device.get("device_id") is not None:
			return str(device["device_id"])
	return "QFw qhw target"


def _sequence(value):
	return isinstance(value, Sequence) and not isinstance(
		value, (str, bytes, bytearray))
