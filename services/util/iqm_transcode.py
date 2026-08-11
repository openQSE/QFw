# Shared OpenQASM -> IQM circuit transcode.
#
# Extracted from svc_iqm_qpm/util_iqm.py so both the native IQM service and the
# QRMI/QDMI shim (svc_lib_qpm) build IQM circuits from OpenQASM the same way.
# build_iqm_circuit(qasm, dynamic_architecture, mapping) is the entry point: it
# serializes via iqm.qiskit_iqm when possible and falls back to a manual
# translation of native OpenQASM gates. dynamic_architecture is the IQM dynamic
# quantum architecture (a dict or an iqm-client object; to_jsonable coerces it).

from defw_exception import DEFwExecutionError
from dataclasses import asdict, is_dataclass
from uuid import UUID
import logging
import math
import re


def to_jsonable(value):
	if value is None or isinstance(value, (str, int, float, bool)):
		return value
	if isinstance(value, UUID):
		return str(value)
	if isinstance(value, dict):
		return {str(k): to_jsonable(v) for k, v in value.items()}
	if isinstance(value, (list, tuple, set, frozenset)):
		return [to_jsonable(v) for v in value]
	if is_dataclass(value):
		return to_jsonable(asdict(value))
	if hasattr(value, "model_dump"):
		return to_jsonable(value.model_dump(mode="json"))
	if hasattr(value, "dict"):
		return to_jsonable(value.dict())
	return str(value)

def load_iqm_pulse_module():
	try:
		from iqm.pulse import Circuit, CircuitOperation
	except Exception as exc:
		raise DEFwExecutionError(
			f"failed to import iqm.pulse circuit objects: {exc}") from exc
	return Circuit, CircuitOperation

def active_qubits(dynamic_architecture):
	data = to_jsonable(dynamic_architecture)
	qubits = data.get("qubits") or []
	if not qubits:
		raise DEFwExecutionError(
			"IQM dynamic architecture did not report active qubits")
	return [str(qubit) for qubit in qubits]

def logical_to_physical_qubits(qasm_circuit, dynamic_architecture, mapping):
	physical = active_qubits(dynamic_architecture)
	num_qubits = getattr(qasm_circuit, "num_qubits", 0)
	if mapping:
		if isinstance(mapping, dict):
			return {
				index: str(mapping.get(index, mapping.get(str(index))))
				for index in range(num_qubits)
			}
		return {index: str(value) for index, value in enumerate(mapping)}

	if num_qubits > len(physical):
		raise DEFwExecutionError(
			f"circuit requires {num_qubits} qubits but IQM reports "
			f"{len(physical)} active qubits")
	return {index: physical[index] for index in range(num_qubits)}

def eval_angle(value):
	allowed = {"pi": math.pi}
	try:
		return float(eval(value, {"__builtins__": {}}, allowed))
	except Exception as exc:
		raise DEFwExecutionError(
			f"unsupported angle expression in OpenQASM: {value!r}") from exc

def split_qasm_statements(qasm):
	statements = []
	for line in qasm.splitlines():
		line = line.split("//", 1)[0].strip()
		if not line:
			continue
		for part in line.split(";"):
			part = part.strip()
			if part:
				statements.append(part)
	return statements

def load_qiskit_circuit(qasm):
	try:
		from qiskit import QuantumCircuit
		try:
			from qiskit import qasm2
			return qasm2.loads(qasm)
		except Exception:
			return QuantumCircuit.from_qasm_str(qasm)
	except Exception as exc:
		raise DEFwExecutionError(
			f"failed to parse OpenQASM through qiskit: {exc}") from exc

def _iqm_backend_calibration_set_id(backend):
	calibration_set_id = getattr(backend, "_calibration_set_id", None)
	return str(calibration_set_id) if calibration_set_id is not None else None

def transpile_qiskit_to_iqm(qasm, client, calibration_set_id=None, mapping=None):
	Circuit, _ = load_iqm_pulse_module()
	try:
		from iqm.qiskit_iqm import IQMBackend, transpile_to_IQM
	except Exception as exc:
		raise DEFwExecutionError(
			"iqm.qiskit_iqm is required to transpile qiskit circuits "
			f"for IQM execution: {exc}") from exc

	qiskit_circuit = load_qiskit_circuit(qasm)
	try:
		backend = IQMBackend(client, calibration_set_id=calibration_set_id)
		restrict_to_qubits = None
		if mapping:
			index_to_name = logical_to_physical_qubits(
				qiskit_circuit, backend.architecture, mapping)
			restrict_to_qubits = list(index_to_name.values())
		transpiled = transpile_to_IQM(
			qiskit_circuit,
			backend,
			restrict_to_qubits=restrict_to_qubits,
		)
		iqm_circuit = backend.serialize_circuit(transpiled)
	except Exception as exc:
		raise DEFwExecutionError(
			"IQM could not transpile the circuit to native operations. "
			f"Error: {exc}") from exc

	metadata = dict(getattr(iqm_circuit, "metadata", None) or {})
	metadata["qfw_transpiled_to_iqm"] = True
	effective_calibration_set_id = _iqm_backend_calibration_set_id(backend)
	if effective_calibration_set_id is not None:
		metadata["iqm_calibration_set_id"] = effective_calibration_set_id
	return Circuit(
		name=iqm_circuit.name or qiskit_circuit.name or "qfw_iqm_circuit",
		instructions=tuple(iqm_circuit.instructions),
		metadata=metadata,
	)

def serialize_qiskit_to_iqm(qasm, dynamic_architecture, mapping):
	Circuit, _ = load_iqm_pulse_module()
	try:
		from iqm.qiskit_iqm import qiskit_to_iqm
	except Exception as exc:
		raise DEFwExecutionError(
			"iqm.qiskit_iqm is required to serialize qiskit circuits "
			f"for IQM execution: {exc}") from exc

	qiskit_circuit = load_qiskit_circuit(qasm)
	index_to_name = logical_to_physical_qubits(
		qiskit_circuit, dynamic_architecture, mapping)
	try:
		instructions = qiskit_to_iqm.serialize_instructions(
			qiskit_circuit, index_to_name)
	except Exception as exc:
		raise DEFwExecutionError(
			"IQM could not serialize the circuit. Transpile the circuit "
			f"to IQM-supported native operations first. Error: {exc}") from exc

	return Circuit(
		name=qiskit_circuit.name or "qfw_iqm_circuit",
		instructions=tuple(instructions),
		metadata={"logical_to_physical": index_to_name},
	)

def parse_ref(ref, qregs):
	ref = ref.strip()
	match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$", ref)
	if match:
		reg, index = match.group(1), int(match.group(2))
		if reg not in qregs or index >= qregs[reg]:
			raise DEFwExecutionError(f"unknown OpenQASM qubit reference {ref}")
		return [(reg, index)]
	if ref in qregs:
		return [(ref, index) for index in range(qregs[ref])]
	raise DEFwExecutionError(f"unknown OpenQASM qubit reference {ref}")

def build_manual_iqm_circuit(qasm, dynamic_architecture, mapping):
	Circuit, CircuitOperation = load_iqm_pulse_module()
	statements = split_qasm_statements(qasm)
	qregs = {}
	cregs = {}
	operations = []

	for statement in statements:
		if statement.startswith("OPENQASM") or statement.startswith("include"):
			continue
		match = re.match(r"^qreg\s+(\w+)\[(\d+)\]$", statement)
		if match:
			qregs[match.group(1)] = int(match.group(2))
			continue
		match = re.match(r"^creg\s+(\w+)\[(\d+)\]$", statement)
		if match:
			cregs[match.group(1)] = int(match.group(2))
			continue

	if not qregs:
		raise DEFwExecutionError("OpenQASM input does not define a qreg")

	total_qubits = sum(qregs.values())
	physical = active_qubits(dynamic_architecture)
	if total_qubits > len(physical):
		raise DEFwExecutionError(
			f"circuit requires {total_qubits} qubits but IQM reports "
			f"{len(physical)} active qubits")

	ordered = []
	for reg, size in qregs.items():
		for index in range(size):
			ordered.append((reg, index))
	if mapping:
		if isinstance(mapping, dict):
			qubit_map = {
				key: str(mapping.get(f"{key[0]}[{key[1]}]",
							 mapping.get(key[1], mapping.get(str(key[1])))))
				for key in ordered
			}
		else:
			qubit_map = {key: str(mapping[index])
				     for index, key in enumerate(ordered)}
	else:
		qubit_map = {key: physical[index] for index, key in enumerate(ordered)}

	for statement in statements:
		if statement.startswith(("OPENQASM", "include", "qreg", "creg")):
			continue
		if statement.startswith("barrier"):
			refs = statement[len("barrier"):].strip()
			locus = []
			for ref in refs.split(","):
				locus.extend(qubit_map[key] for key in parse_ref(ref, qregs))
			operations.append(CircuitOperation(
				name="barrier", locus=tuple(locus), args={}))
			continue
		if statement.startswith("measure"):
			match = re.match(r"^measure\s+(.+)\s+->\s+(.+)$", statement)
			if not match:
				raise DEFwExecutionError(
					f"unsupported OpenQASM measurement: {statement}")
			qrefs = parse_ref(match.group(1), qregs)
			cref = match.group(2).strip()
			key = "m"
			match_cref = re.match(r"^(\w+)\[(\d+)\]$", cref)
			if match_cref:
				key = f"{match_cref.group(1)}{match_cref.group(2)}"
			elif cref not in cregs:
				raise DEFwExecutionError(
					f"unknown OpenQASM classical reference {cref}")
			operations.append(CircuitOperation(
				name="measure",
				locus=tuple(qubit_map[key] for key in qrefs),
				args={"key": key},
			))
			continue
		match = re.match(r"^(\w+)(?:\(([^)]*)\))?\s+(.+)$", statement)
		if not match:
			raise DEFwExecutionError(
				f"unsupported OpenQASM statement: {statement}")
		gate, params, refs = match.group(1), match.group(2), match.group(3)
		refs = [ref.strip() for ref in refs.split(",")]
		locus = tuple(qubit_map[key] for ref in refs for key in parse_ref(ref, qregs))
		if gate == "x":
			operations.append(CircuitOperation(
				name="prx", locus=locus, args={"angle": math.pi,
							       "phase": 0.0}))
		elif gate == "rx" and params is not None:
			operations.append(CircuitOperation(
				name="prx", locus=locus, args={"angle": eval_angle(params),
							       "phase": 0.0}))
		elif gate == "ry" and params is not None:
			operations.append(CircuitOperation(
				name="prx", locus=locus, args={"angle": eval_angle(params),
							       "phase": math.pi / 2}))
		elif gate == "cz":
			operations.append(CircuitOperation(
				name="cz", locus=locus, args={}))
		else:
			raise DEFwExecutionError(
				"IQM service can only manually translate native "
				f"OpenQASM gates x, rx, ry, cz, barrier, measure. "
				f"Unsupported statement: {statement}")

	return Circuit(
		name="qfw_iqm_circuit",
		instructions=tuple(operations),
		metadata={"logical_to_physical": to_jsonable(qubit_map)},
	)

def build_iqm_circuit(qasm, dynamic_architecture, mapping,
		      client=None, calibration_set_id=None):
	if client is not None:
		try:
			return transpile_qiskit_to_iqm(
				qasm,
				client,
				calibration_set_id=calibration_set_id,
				mapping=mapping,
			)
		except DEFwExecutionError as exc:
			logging.debug(
				f"falling back to direct IQM QASM serialization: {exc}")
	try:
		return serialize_qiskit_to_iqm(qasm, dynamic_architecture, mapping)
	except DEFwExecutionError as exc:
		logging.debug(f"falling back to manual IQM QASM translation: {exc}")
		return build_manual_iqm_circuit(qasm, dynamic_architecture, mapping)
