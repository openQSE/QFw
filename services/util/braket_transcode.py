# Shared Qiskit -> Amazon Braket OpenQASM 3 transcode utilities.
#
# Used by the QDMI driver's Braket profile (svc_lib_qpm/drivers/qdmi_profiles.py)
# to turn the circuit a client sent, QPY or OpenQASM 2, into the self-contained
# OpenQASM 3 program the Amazon Braket QDMI device library submits as a Braket
# quantum task, and to key the counts that come back onto the circuit's
# classical bits.
#
# Braket compiles and routes a non-verbatim program on its side, so no coupling
# map is applied here. What has to hold is the vocabulary: every gate must be
# one the device accepts, under Braket's name for it. A gate the device does
# not accept is decomposed into ones it does, and anything still unexpressible
# fails here, before a task is created and billed.
#
# Qiskit is imported where it is used, so this module imports without it.

from defw_exception import DEFwExecutionError
from util.gate_names import INSTRUCTION_ALIASES

# Measurement and barrier are output/directive syntax. Reset needs explicit
# device support, just like a gate. Barriers are kept: Braket's compilers do
# not reorder or merge gates across them, so dropping one can change what
# runs.
DIRECTIVES = frozenset({"barrier", "measure"})

# The technology family of a Braket device, from the vendor segment of its
# ARN: arn:aws:braket:<region>::device/qpu/<vendor>/<name>, or
# arn:aws:braket:::device/quantum-simulator/amazon/<name>. The values are the
# qhw-device-v1 vocabulary.
VENDOR_TECHNOLOGY = {
	"rigetti": "superconducting",
	"iqm": "superconducting",
	"ionq": "trapped-ion",
	"aqt": "trapped-ion",
	"quera": "neutral-atom",
}


def technology_for_arn(arn):
	"""The qhw technology family for a Braket device ARN, or None."""
	parts = str(arn or "").split("/")
	if "quantum-simulator" in parts:
		return "simulator"
	if "qpu" in parts:
		index = parts.index("qpu")
		if index + 1 < len(parts):
			return VENDOR_TECHNOLOGY.get(parts[index + 1].lower())
	return None


def accepted_operation_names(device):
	"""The gate names this Braket device takes, lowercased.

	QDMI's device operations are the QPU's native gate set (for SV1 and DM1,
	the executable set). The Braket device library puts Braket's broader
	supportedOperations set in device CUSTOM1 as operation handles. A
	non-verbatim program may use any of them, since Braket compiles it.
	"""
	names = set()
	for operation in _operations(device):
		name = _operation_name(operation)
		if name:
			names.add(name.lower())
	query = getattr(device, "query_custom_operations", None)
	if query is not None:
		try:
			from mqt.core.qdmi import CustomProperty
			extra = query(CustomProperty.CUSTOM1) or []
		except Exception:
			extra = []
		for operation in extra:
			name = _operation_name(operation)
			if name:
				names.add(name.lower())
	if not names:
		raise DEFwExecutionError(
			"the Braket device reported no operations, so no program can "
			"be checked against it")
	return names


def to_braket_qasm3(circuit, backend):
	"""Return OpenQASM 3 and the map from measured qubits to classical bits.

	Measure qubits in ascending order into a compact classical register so
	qubit order and register order agree across Amazon Braket backends. The
	returned map restores the circuit's original classical-bit layout.
	"""
	try:
		from qiskit.circuit import ClassicalRegister, QuantumCircuit, QuantumRegister
		from qiskit.converters import circuit_to_dag, dag_to_circuit
	except ImportError as exc:
		raise DEFwExecutionError(
			f"Braket serialization needs the QDMI package set: {exc}") from exc

	accepted = accepted_operation_names(backend.device)
	aliases = {}
	for name in sorted(accepted):
		aliases.setdefault(INSTRUCTION_ALIASES.get(name, name), name)

	# Braket's Target contains only native gates. Its non-verbatim compiler
	# also accepts supportedOperations (CUSTOM1).
	# https://github.com/Munich-Quantum-Software/amazon-braket-qdmi-device/issues/207
	def braket_name(name):
		name = name.lower()
		if name in accepted:
			return name
		return aliases.get(name, name)

	def unaccepted(circ):
		return sorted({
			instruction.operation.name for instruction in circ.data
			if instruction.operation.name.lower() not in DIRECTIVES
			and braket_name(instruction.operation.name) not in accepted})

	if circuit.parameters:
		raise DEFwExecutionError("Braket circuits must have bound parameters")
	# The driver reports final qubit values, so it cannot recover
	# an earlier measurement followed by a gate.
	# https://github.com/Munich-Quantum-Software/amazon-braket-qdmi-device/pull/242
	measured_qubits = set()
	for instruction in circuit.data:
		name = instruction.operation.name.lower()
		if name == "measure":
			measured_qubits.update(instruction.qubits)
		elif name != "barrier" and measured_qubits.intersection(instruction.qubits):
			raise DEFwExecutionError(
				"Braket's legacy QDMI results require terminal measurements")

	if unaccepted(circuit):
		circuit = _decompose(circuit, accepted, aliases)
		if missing := unaccepted(circuit):
			raise DEFwExecutionError(
				f"the device accepts none of {missing}, and they could not be "
				f"decomposed into its operation set {sorted(accepted)}")

	num_clbits = circuit.num_clbits
	dag = circuit_to_dag(circuit)
	dag.remove_qubits(*(set(dag.idle_wires()) & set(dag.qubits)))
	trimmed = dag_to_circuit(dag)

	# Keep gate order and hold measurements for the final canonical block.
	measurement_map = []
	measured = set()
	operations = []
	used = set()
	for instruction in trimmed.data:
		name = instruction.operation.name
		qubits = [trimmed.find_bit(qubit).index for qubit in instruction.qubits]
		if name.lower() == "measure":
			measurement_map.append(
				(qubits[0], trimmed.find_bit(instruction.clbits[0]).index))
			measured.add(qubits[0])
			continue
		if name.lower() != "barrier":
			after = sorted(measured.intersection(qubits))
			if after:
				raise DEFwExecutionError(
					"Braket takes terminal measurements only: "
					f"{name} on qubit(s) {after} follows their measurement")
			if instruction.clbits:
				raise DEFwExecutionError(
					f"{name} reads classical bits, which a Braket program "
					"cannot do")
		operation = instruction.operation
		if name.lower() not in DIRECTIVES:
			new_name = braket_name(name)
			used.add(new_name)
			if new_name != name:
				operation = operation.copy(name=new_name)
		operations.append((operation, qubits))

	program_circuit = QuantumCircuit()
	if trimmed.num_qubits:
		qreg = QuantumRegister(trimmed.num_qubits, "q")
		program_circuit.add_register(qreg)
		for operation, qubits in operations:
			program_circuit.append(
				operation, [qreg[index] for index in qubits])
		ranks = sorted(measured)
		if ranks:
			creg = ClassicalRegister(len(ranks), "c")
			program_circuit.add_register(creg)
			for rank, qubit in enumerate(ranks):
				program_circuit.measure(qreg[qubit], creg[rank])

	from qiskit import qasm3
	program = qasm3.dumps(program_circuit, includes=(), basis_gates=sorted(used))
	return program, {"num_clbits": num_clbits, "map": measurement_map}


def remap_counts(counts, measurement):
	"""Key the Braket device library's histogram by classical bit.

	The library writes each key with the highest-index measured qubit
	leftmost, over the measured qubits in ascending order, which
	to_braket_qasm3's measurement block guarantees on every Braket backend.
	Qiskit keys by classical bit, highest index leftmost, over every bit of
	the registers. The two agree only when bit i holds qubit i and every
	qubit is measured, so rebuild each key from the measurement map recorded
	at transcode time. An unmeasured bit reads 0, as it does in Qiskit; a
	qubit measured into several bits fills each of them, and the last
	measurement into a bit is the one that lands.
	"""
	# Remove this when the provider returns counts in classical-bit order.
	# Applying it again would corrupt permuted outputs.
	# https://github.com/Munich-Quantum-Software/amazon-braket-qdmi-device/pull/242
	if not counts or not isinstance(measurement, dict):
		return counts
	pairs = list(measurement.get("map") or [])
	num_clbits = int(measurement.get("num_clbits") or 0)
	if not pairs or not num_clbits:
		return counts
	measured = sorted({qubit for qubit, _clbit in pairs})
	# Where each measured qubit's bit sits in a library key: position 0 holds
	# the highest measured qubit.
	position = {
		qubit: len(measured) - 1 - rank for rank, qubit in enumerate(measured)}
	remapped = {}
	for key, count in counts.items():
		bits = str(key)
		if len(bits) != len(measured):
			raise DEFwExecutionError(
				f"Braket counts key {key!r} does not cover the "
				f"{len(measured)} measured qubits")
		out = ["0"] * num_clbits
		for qubit, clbit in pairs:
			out[num_clbits - 1 - clbit] = bits[position[qubit]]
		new_key = "".join(out)
		remapped[new_key] = remapped.get(new_key, 0) + count
	return remapped


def _decompose(circuit, accepted, aliases):
	# Rewrite into Qiskit gates whose Braket spelling the device accepts. Only
	# standard gates can be a transpile basis; a Braket-only name (gpi, ms,
	# ...) has no Qiskit gate to decompose into and is left to the rename.
	from qiskit import transpile
	from qiskit.circuit.library import get_standard_gate_name_mapping
	standard = set(get_standard_gate_name_mapping()) - DIRECTIVES
	basis = sorted(
		name for name in standard
		if aliases.get(name, name) in accepted)
	if not basis:
		return circuit
	try:
		return transpile(
			circuit, basis_gates=basis + ["measure"], optimization_level=1)
	except Exception as exc:
		raise DEFwExecutionError(
			"could not decompose the circuit into the device's operation set "
			f"{sorted(accepted)}: {exc}") from exc


def _operations(device):
	# The first query that reaches AWS. Its failure is the one to report:
	# without credentials or network the library says "Permission denied"
	# here, and swallowing it would turn into a misleading complaint about
	# the circuit's gates further on.
	try:
		return list(device.operations() or [])
	except Exception as exc:
		raise DEFwExecutionError(
			f"could not read the Braket device's operations: {exc}") from exc


def _operation_name(operation):
	try:
		return operation.name()
	except Exception:
		return None
