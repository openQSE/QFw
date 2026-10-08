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

# Qiskit gate name -> Braket OpenQASM name, where the two differ. Braket's
# dialect spells several standard gates its own way; the rest coincide. These
# are the gate aliases of MQSC's amazon.braket.qdmi.qiskit adapter.
QISKIT_TO_BRAKET = {
	"cx": "cnot",
	"ccx": "ccnot",
	"cp": "cphaseshift",
	"p": "phaseshift",
	"rxx": "xx",
	"ryy": "yy",
	"rzz": "zz",
	"sdg": "si",
	"sx": "v",
	"sxdg": "vi",
	"tdg": "ti",
	"id": "i",
}
BRAKET_TO_QISKIT = {
	braket: qiskit for qiskit, braket in QISKIT_TO_BRAKET.items()}

# Not gates. They pass through whatever the device's operation set says.
DIRECTIVES = frozenset({"barrier", "measure", "reset"})

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


def to_braket_qasm3(circuit, accepted):
	"""Serialize a Qiskit circuit as Braket's self-contained OpenQASM 3.

	Returns (program, measurement). `measurement` is what remap_counts needs
	to key the device library's histogram onto the circuit: the circuit's
	classical bit count and the (program qubit, classical bit) pairs of its
	measurements.

	Gates the device spells differently are renamed. Gates it does not accept
	are decomposed into ones it does, with no coupling map, because Braket
	routes a non-verbatim program on its side. Idle qubits are dropped, since
	the simulators want contiguous indices.
	"""
	accepted = {str(name).lower() for name in accepted}
	try:
		from qiskit import qasm3
		from qiskit.circuit import QuantumRegister
		from qiskit.converters import circuit_to_dag, dag_to_circuit
	except Exception as exc:
		raise DEFwExecutionError(
			f"transcoding to Braket OpenQASM 3 needs qiskit: {exc}") from exc

	def braket_name(name):
		name = name.lower()
		return name if name in accepted else QISKIT_TO_BRAKET.get(name, name)

	def unaccepted(circ):
		return sorted({
			instruction.operation.name for instruction in circ.data
			if instruction.operation.name.lower() not in DIRECTIVES
			and braket_name(instruction.operation.name) not in accepted})

	missing = unaccepted(circuit)
	if missing:
		circuit = _decompose(circuit, accepted)
		still = unaccepted(circuit)
		if still:
			raise DEFwExecutionError(
				f"the device accepts none of {still}, and they could not be "
				f"decomposed into its operation set {sorted(accepted)}")

	num_clbits = circuit.num_clbits
	dag = circuit_to_dag(circuit)
	dag.remove_qubits(*(set(dag.idle_wires()) & set(dag.qubits)))
	program_circuit = dag_to_circuit(dag)
	if program_circuit.qubits and not program_circuit.qregs:
		program_circuit.add_register(
			QuantumRegister(bits=program_circuit.qubits, name="q"))
	measurement_map = []
	used = set()
	for index, instruction in enumerate(program_circuit.data):
		name = instruction.operation.name
		if name.lower() == "measure":
			measurement_map.append((
				program_circuit.find_bit(instruction.qubits[0]).index,
				program_circuit.find_bit(instruction.clbits[0]).index))
			continue
		if name.lower() in DIRECTIVES:
			continue
		new_name = braket_name(name)
		used.add(new_name)
		if new_name != name:
			program_circuit.data[index] = instruction.replace(
				operation=instruction.operation.copy(name=new_name))
	# Every gate is declared as basis, so the exporter writes none of them
	# out as a definition in terms of U, which Braket would not take.
	program = qasm3.dumps(
		program_circuit, includes=(), basis_gates=sorted(used))
	return program, {"num_clbits": num_clbits, "map": measurement_map}


def remap_counts(counts, measurement):
	"""Key the Braket device library's histogram by classical bit.

	The library writes each key with the highest-index measured qubit
	leftmost, over the measured qubits in ascending order. Qiskit keys by
	classical bit, highest index leftmost, over every bit of the registers.
	The two agree only when bit i holds qubit i and every qubit is measured,
	so rebuild each key from the measurement map recorded at transcode time.
	An unmeasured bit reads 0, as it does in Qiskit.
	"""
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
	clbit_of = {}
	for qubit, clbit in pairs:
		# The last measurement of a qubit is the one that lands, as in Qiskit.
		clbit_of[qubit] = clbit
	remapped = {}
	for key, count in counts.items():
		bits = str(key)
		if len(bits) != len(measured):
			raise DEFwExecutionError(
				f"Braket counts key {key!r} does not cover the "
				f"{len(measured)} measured qubits")
		out = ["0"] * num_clbits
		for qubit, clbit in clbit_of.items():
			out[num_clbits - 1 - clbit] = bits[position[qubit]]
		new_key = "".join(out)
		remapped[new_key] = remapped.get(new_key, 0) + count
	return remapped


def _decompose(circuit, accepted):
	# Rewrite into Qiskit gates whose Braket spelling the device accepts. Only
	# standard gates can be a transpile basis; a Braket-only name (gpi, ms,
	# ...) has no Qiskit gate to decompose into and is left to the rename.
	from qiskit import transpile
	from qiskit.circuit.library import get_standard_gate_name_mapping
	standard = set(get_standard_gate_name_mapping()) - DIRECTIVES
	basis = sorted(
		{BRAKET_TO_QISKIT.get(name, name) for name in accepted} & standard)
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
