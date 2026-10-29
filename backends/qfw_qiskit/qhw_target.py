"""Transitional qhw-to-Qiskit target conversion.

Remove this helper when Qiskit consumes standardized qhw records directly.
The module has no QFw service, reservation, credential, or execution imports.
"""

from collections.abc import Mapping
from importlib import metadata

from qfw_target_data import (
	calibration_metrics,
	directed,
	locus_metric,
	nodes,
	operation_loci,
	operations,
)


SUPPORTED_QISKIT_VERSIONS = frozenset({"2.2.3"})

INSTRUCTION_ALIASES = {
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

SYMMETRIC_OPERATIONS = frozenset({
	"cz", "ccz", "swap", "iswap", "rxx", "ryy", "rzz", "cp", "cs",
	"csdg",
})

SKIPPED_OPERATIONS = frozenset({
	"barrier", "cc_prx", "measure_fidelity", "prx_12",
})


def qhw_to_qiskit_target(qhw_device, qhw_coupling, qhw_calibration=None):
	"""Build a Qiskit ``Target`` from authoritative qhw mappings."""
	version = metadata.version("qiskit")
	if version not in SUPPORTED_QISKIT_VERSIONS:
		raise RuntimeError(f"unsupported Qiskit version {version}")
	from qiskit.transpiler import InstructionProperties, Target

	node_ids = nodes(qhw_device, qhw_coupling)
	index_by_node = {node: index for index, node in enumerate(node_ids)}
	target = Target(
		description="QFw qhw target",
		num_qubits=len(node_ids),
		**_qubit_properties(node_ids, qhw_calibration),
	)
	qhw_operations = operations(qhw_device, qhw_coupling)
	if not qhw_operations:
		raise ValueError("qhw records contain no operations")
	metrics, _qubits = calibration_metrics(qhw_calibration)
	added = set()
	for operation in qhw_operations:
		reported_name = str(operation.get("name") or "")
		if not reported_name:
			raise ValueError("qhw operation has no name")
		if reported_name in SKIPPED_OPERATIONS:
			continue
		instruction = _qiskit_instruction(operation)
		if instruction.name in added:
			raise ValueError(
				f"qhw operations map to duplicate instruction {instruction.name!r}")
		loci = operation_loci(operation, index_by_node)
		symmetric = (
			len(loci[0]) == 2 and instruction.name in SYMMETRIC_OPERATIONS and
			not directed(qhw_coupling)) if loci else False
		properties = {}
		for locus in loci:
			labels = tuple(node_ids[index] for index in locus)
			values = _operation_metric(operation, labels)
			values.update(locus_metric(
				metrics, reported_name, labels, symmetric=symmetric))
			property_value = None
			if values:
				property_value = InstructionProperties(
					duration=values.get("duration"),
					error=values.get("error"),
				)
			properties[locus] = property_value
			if symmetric:
				properties.setdefault(tuple(reversed(locus)), property_value)
		target.add_instruction(instruction, properties, name=instruction.name)
		added.add(instruction.name)
	return target


def _qiskit_instruction(operation):
	from qiskit.circuit import Gate, Measure, Parameter, Reset
	from qiskit.circuit.library import get_standard_gate_name_mapping

	reported_name = str(operation["name"])
	name = INSTRUCTION_ALIASES.get(reported_name, reported_name)
	if name == "measure":
		return Measure()
	if name == "reset":
		return Reset()
	catalog = get_standard_gate_name_mapping()
	instruction = catalog.get(name)
	if instruction is not None:
		return instruction
	parameters = operation.get("parameters") or ()
	parameter_names = []
	for index, parameter in enumerate(parameters):
		if isinstance(parameter, Mapping):
			parameter = parameter.get("name")
		parameter_names.append(str(parameter or f"p{index}"))
	try:
		arity = int(operation.get("arity"))
	except (TypeError, ValueError) as exc:
		raise ValueError(
			f"qhw operation {reported_name!r} has no valid arity") from exc
	return Gate(name, arity, [Parameter(value) for value in parameter_names])


def _operation_metric(operation, labels):
	properties = operation.get("properties_by_locus") or {}
	if not isinstance(properties, Mapping):
		return {}
	value = properties.get(",".join(labels)) or properties.get(tuple(labels))
	return dict(value) if isinstance(value, Mapping) else {}


def _qubit_properties(node_ids, calibration):
	_metrics, qubits = calibration_metrics(calibration)
	if not qubits:
		return {}
	try:
		from qiskit.providers import QubitProperties
	except ImportError:
		return {}
	return {"qubit_properties": [
		QubitProperties(
			t1=(qubits.get(node) or {}).get("t1"),
			t2=(qubits.get(node) or {}).get("t2"),
		)
		for node in node_ids
	]}
