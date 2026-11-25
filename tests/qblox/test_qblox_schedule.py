"""
build_schedule() against the real qblox-scheduler and Qiskit.

The pure-Python half of this service's tests (parsing, counts, identity)
lives in tests/mock/test_qblox_qpm.py and runs without the Qblox stack.
These need the real hardware, so ci-qblox runs them.
"""

import pytest

from svc_qblox_qpm import util_qblox


# The star topology of configs/dev_cfg.json: q0_q1 and q0_q2 are edges,
# q1_q2 is not.
STAR = [[0, 1], [1, 0], [0, 2], [2, 0]]


def _cz_pairs(schedule):
	"""Every qubit pair a CZ in the compiled schedule acts on."""
	pairs = []
	for operation in schedule.operations.values():
		info = getattr(operation, "data", {}).get("gate_info", {})
		if str(info.get("operation_type", "")).upper() != "CZ":
			continue
		elements = info.get("device_elements", [])
		if len(elements) == 2:
			pairs.append(
				frozenset(int(str(q).lstrip("q")) for q in elements))
	return pairs


def test_build_schedule_maps_each_measure_to_its_clbit():
	from qiskit import QuantumCircuit

	# Crossed over: qubit 0 writes clbit 2, qubit 2 writes clbit 0.
	circuit = QuantumCircuit(3, 3)
	circuit.x(0)
	circuit.measure(0, 2)
	circuit.measure(2, 0)

	_schedule, acq_channel_to_clbit = util_qblox.build_schedule(
		circuit, shots=16)

	# Two measurements, two distinct channels, landing on clbits 2 and 0.
	assert len(acq_channel_to_clbit) == 2
	assert sorted(acq_channel_to_clbit.values()) == [0, 2]


def test_counts_from_a_crossed_over_measure_follow_qiskit_bit_order():
	"""
	End to end over the two halves of note 2: the mapping build_schedule
	produces, fed to build_counts. X on qubit 0 written to clbit 2 must read
	as "100", not "001".
	"""
	from qiskit import QuantumCircuit

	circuit = QuantumCircuit(3, 3)
	circuit.x(0)
	circuit.measure(0, 2)
	circuit.measure(2, 0)

	_schedule, acq_channel_to_clbit = util_qblox.build_schedule(
		circuit, shots=1)

	# Channel on clbit 2 reads |1>, the other reads |0>.
	channel_values = {
		channel: [1.0 if clbit == 2 else -1.0]
		for channel, clbit in acq_channel_to_clbit.items()}

	counts = util_qblox.build_counts(
		channel_values, shots=1,
		acq_channel_to_clbit=acq_channel_to_clbit, num_clbits=3)

	assert counts == {"100": 1}


def test_build_schedule_without_a_coupling_map_can_emit_a_missing_edge():
	"""
	Why the coupling map is needed: a 3-qubit GHZ entangles neighbours, so
	the unrouted rewrite asks for a CZ on (1, 2), which the star device has
	no edge for.
	"""
	from qiskit import QuantumCircuit

	circuit = QuantumCircuit(3, 3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)
	circuit.measure(range(3), range(3))

	schedule, _ = util_qblox.build_schedule(circuit, shots=16)

	pairs = _cz_pairs(schedule)
	assert pairs, "expected the GHZ rewrite to emit CZs"
	assert frozenset({1, 2}) in pairs


def test_build_schedule_respects_the_coupling_map():
	from qiskit import QuantumCircuit

	circuit = QuantumCircuit(3, 3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)
	circuit.measure(range(3), range(3))

	schedule, acq_channel_to_clbit = util_qblox.build_schedule(
		circuit, shots=16, coupling_map=STAR)

	allowed = {frozenset(pair) for pair in STAR}
	pairs = _cz_pairs(schedule)
	assert pairs, "expected the GHZ rewrite to emit CZs"
	for pair in pairs:
		assert pair in allowed, (
			f"CZ on {sorted(pair)}, which is not an edge of the star device")

	# Routing may permute qubits, so all three clbits must still be covered.
	assert sorted(acq_channel_to_clbit.values()) == [0, 1, 2]


def test_build_schedule_rejects_an_instruction_it_cannot_lower():
	"""
	A delay survives the basis rewrite, so it reaches build_schedule's
	dispatch and must be refused rather than silently dropped - dropping it
	would change the circuit's meaning without saying so.
	"""
	from defw_exception import DEFwExecutionError
	from qiskit import QuantumCircuit

	circuit = QuantumCircuit(1, 1)
	circuit.delay(100, 0)
	circuit.measure(0, 0)

	with pytest.raises(DEFwExecutionError, match="unsupported gate"):
		util_qblox.build_schedule(circuit, shots=1)


def test_build_schedule_names_qubits_from_the_device_element_order():
	"""
	The coupling map is indexed by position in dev_cfg's element list, so
	the schedule has to name qubits from that same list. A config whose
	elements are not q0..qN-1 in order would otherwise constrain one set of
	qubits and drive another.
	"""
	from qiskit import QuantumCircuit

	circuit = QuantumCircuit(2, 2)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.measure(range(2), range(2))

	# A device whose elements are named and ordered unconventionally.
	names = ["qA", "qB"]
	schedule, _ = util_qblox.build_schedule(
		circuit, shots=4, coupling_map=[[0, 1], [1, 0]], qubit_names=names)

	used = set()
	for operation in schedule.operations.values():
		info = getattr(operation, "data", {}).get("gate_info", {})
		used.update(str(q) for q in info.get("device_elements", []))

	assert used <= set(names), f"schedule named qubits outside {names}: {used}"
	assert used, "expected the schedule to name some qubits"


def test_build_schedule_refuses_a_circuit_wider_than_the_device():
	from defw_exception import DEFwExecutionError
	from qiskit import QuantumCircuit

	circuit = QuantumCircuit(3, 3)
	circuit.h(0)
	circuit.measure(range(3), range(3))

	with pytest.raises(DEFwExecutionError, match="names only"):
		util_qblox.build_schedule(circuit, shots=1, qubit_names=["q0", "q1"])
