# Real Qiskit through util.braket_transcode: the OpenQASM 3 Amazon Braket
# takes, in the gate names a device accepts, with the measurement map the
# result remap needs. Needs qiskit, which the CI mock job does not install,
# so this runs in QFw's own venv.

import pathlib
import re
import sys
import types

import pytest
from qiskit import QuantumCircuit, qasm2

AmazonBraketBackend = pytest.importorskip(
	"amazon.braket.qdmi.qiskit").AmazonBraketBackend


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)


from defw_exception import DEFwExecutionError  # noqa: E402
from util.braket_transcode import remap_counts, to_braket_qasm3  # noqa: E402


# A Cepheus-like vocabulary: native rx, ry, cz plus Braket's broader set.
BROAD = {"rx", "ry", "cz", "rz", "h", "cnot", "x", "y", "z", "s", "si", "t",
	"ti", "v", "vi", "swap", "phaseshift", "cphaseshift"}
RESTRICTED = {"rx", "ry", "cz"}


class _Device:
	def __init__(self, names):
		self.names = names

	def operations(self):
		return [types.SimpleNamespace(name=lambda n=n: n) for n in self.names]


def _backend(accepted, native=None):
	# Skip live session/Target construction while exercising the released
	# provider serializer. CUSTOM1 can be broader than the native gates.
	backend = object.__new__(AmazonBraketBackend)
	backend._device = _Device(accepted if native is None else native)
	backend._device.query_custom_operations = lambda _slot: [
		types.SimpleNamespace(name=lambda n=n: n) for n in accepted]
	return backend


def _ghz():
	circuit = QuantumCircuit(3, 3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)
	circuit.sdg(2)
	circuit.sx(1)
	circuit.barrier()
	circuit.measure(range(3), range(3))
	return circuit


def _gate_lines(program):
	# Gate statements only: no header, declarations, barrier or measurement.
	lines = []
	for line in program.splitlines():
		line = line.strip()
		if not line or line.startswith(("OPENQASM", "bit", "qubit", "barrier")):
			continue
		if "measure" in line:
			continue
		lines.append(line)
	return lines


def _gate_names(program):
	return {re.match(r"[a-z_0-9]+", line).group(0) for line in _gate_lines(program)}


def _measure_lines(program):
	return [line.strip() for line in program.splitlines() if "measure" in line]


def _library_keys(program, values):
	# What a device library would make of one ideal shot, for a backend that
	# orders result columns by qubit index (SV1) and for one that orders them
	# by classical register (Rigetti). Modelled on amazon-braket-qdmi 1.3.0,
	# which reversed each row as it came without reading measuredQubits. 1.3.1
	# reads it and keys by qubit index everywhere, so the two orders below now
	# agree at the library too. They are both kept: the point of the canonical
	# measurement block is that QFw does not depend on which one a library
	# picks.
	pairs = [
		(int(re.search(r"c\[(\d+)\]", line).group(1)),
			int(re.search(r"q\[(\d+)\]", line).group(1)))
		for line in _measure_lines(program)]
	by_qubit = "".join(
		str(values[qubit]) for _bit, qubit in sorted(pairs, key=lambda p: p[1]))
	by_register = "".join(
		str(values[qubit]) for _bit, qubit in sorted(pairs, key=lambda p: p[0]))
	return by_qubit[::-1], by_register[::-1]


def test_a_broad_vocabulary_only_renames():
	program, measurement = to_braket_qasm3(_ghz(), _backend(BROAD))

	assert program.startswith("OPENQASM 3.0;")
	assert "include" not in program
	assert "cnot q[0], q[1];" in program
	assert "cnot q[1], q[2];" in program
	assert "v q[1];" in program
	assert "si q[2];" in program
	assert "barrier q[0], q[1], q[2];" in program
	assert "c[0] = measure q[0];" in program
	assert "c[2] = measure q[2];" in program
	# Nothing is written out as a gate definition in terms of U.
	assert "gate " not in program
	assert measurement == {"num_clbits": 3, "map": [(0, 0), (1, 1), (2, 2)]}


def test_a_restricted_vocabulary_decomposes_then_renames():
	program, measurement = to_braket_qasm3(_ghz(), _backend(RESTRICTED))

	assert _gate_names(program) <= RESTRICTED
	assert "cz" in _gate_names(program)
	assert "c[0] = measure q[0];" in program
	assert "barrier q[0], q[1], q[2];" in program
	assert measurement["num_clbits"] == 3
	assert sorted(measurement["map"]) == [(0, 0), (1, 1), (2, 2)]


def test_openqasm2_input_is_read_through_the_shared_loader():
	from util.iqm_transcode import load_qiskit_circuit
	circuit = load_qiskit_circuit(qasm2.dumps(_ghz()))

	program, _measurement = to_braket_qasm3(circuit, _backend(BROAD))

	assert "cnot q[0], q[1];" in program


def test_an_unrepresentable_circuit_fails_before_submission():
	circuit = QuantumCircuit(3)
	circuit.ccx(0, 1, 2)
	circuit.measure_all()

	# A device with no two-qubit gate cannot take a Toffoli.
	with pytest.raises(DEFwExecutionError, match="operation set"):
		to_braket_qasm3(circuit, _backend({"rx"}))


def test_braket_only_names_pass_when_the_device_accepts_them():
	# A circuit already in a device's own vocabulary (an IonQ-like set) is
	# passed through by name, with no decomposition attempted.
	from qiskit.circuit import Gate
	circuit = QuantumCircuit(2, 2)
	circuit.append(Gate("gpi", 1, [0.5]), [0])
	circuit.append(Gate("ms", 2, [0.0, 0.0, 0.25]), [0, 1])
	circuit.measure([0, 1], [0, 1])

	program, _measurement = to_braket_qasm3(circuit, _backend({"gpi", "gpi2", "ms"}))

	assert "gpi(0.5) q[0];" in program
	# Qiskit's exporter prints a zero parameter as 0.
	assert "ms(0, 0, 0.25) q[0], q[1];" in program


def test_idle_qubits_are_dropped_and_the_map_follows():
	circuit = QuantumCircuit(3, 1)
	circuit.x(2)
	circuit.measure(2, 0)

	program, measurement = to_braket_qasm3(circuit, _backend(BROAD))

	assert "qubit[1] q;" in program
	assert "x q[0];" in program
	assert measurement == {"num_clbits": 1, "map": [(0, 0)]}


def test_the_asymmetric_smoke_circuit_remaps_to_classical_bit_order():
	# X on qubit 0, measured out of qubit order into permuted bits. Every
	# ideal shot reads "100" in Qiskit's order. The library keys the three
	# measured qubits highest first, which reads "001" for that shot.
	circuit = QuantumCircuit(3, 3)
	circuit.x(0)
	circuit.measure(2, 0)
	circuit.measure(0, 2)
	circuit.measure(1, 1)

	program, measurement = to_braket_qasm3(circuit, _backend(BROAD))

	# The program measures in ascending qubit order into a fresh register,
	# whatever the circuit's bit assignment, so bit order, statement order
	# and qubit order coincide. The map carries the circuit's bits.
	assert _measure_lines(program) == [
		"c[0] = measure q[0];", "c[1] = measure q[1];", "c[2] = measure q[2];"]
	# The DAG round trip may reorder independent measurements; the remap
	# reads the map as a set of (qubit, bit) pairs, so order is immaterial.
	assert measurement["num_clbits"] == 3
	assert sorted(measurement["map"]) == [(0, 2), (1, 1), (2, 0)]
	# Both column orders give the same row for the ideal shot (q0 = 1), the
	# library keys it "001", and the remap reads that as Qiskit's "100".
	assert _library_keys(program, {0: 1, 1: 0, 2: 0}) == ("001", "001")
	assert remap_counts({"001": 10}, measurement) == {"100": 10}


def test_broader_supported_operations_use_the_provider_aliases():
	program, _measurement = to_braket_qasm3(
		_ghz(), _backend(BROAD, native=RESTRICTED))
	assert "cnot q[0], q[1];" in program
	assert "v q[1];" in program
	assert "si q[2];" in program


@pytest.mark.parametrize("unsupported", ["reset", "mid-circuit", "unbound"])
def test_unsupported_inputs_fail_before_native_submission(unsupported):
	from qiskit.circuit import Parameter
	circuit = QuantumCircuit(1, 1)
	if unsupported == "reset":
		circuit.reset(0)
	elif unsupported == "mid-circuit":
		circuit.measure(0, 0)
		circuit.x(0)
	else:
		circuit.rx(Parameter("theta"), 0)
	circuit.measure(0, 0)
	with pytest.raises(DEFwExecutionError):
		to_braket_qasm3(circuit, _backend(BROAD))


def test_repeated_terminal_measurement_keeps_both_classical_destinations():
	circuit = QuantumCircuit(1, 2)
	circuit.x(0)
	circuit.measure(0, 0)
	circuit.measure(0, 1)
	_program, measurement = to_braket_qasm3(circuit, _backend(BROAD))
	assert remap_counts({"1": 10}, measurement) == {"11": 10}


def test_one_qubit_can_fill_multiple_classical_bits():
	measurement = {"num_clbits": 3, "map": [(0, 0), (0, 2)]}
	assert remap_counts({"1": 10}, measurement) == {"101": 10}


def test_gates_on_unmeasured_qubits_can_follow_a_measurement():
	circuit = QuantumCircuit(2, 2)
	circuit.measure(0, 0)
	circuit.h(1)
	circuit.measure(1, 1)
	program, _measurement = to_braket_qasm3(circuit, _backend(BROAD))
	assert "h q[1];" in program


def test_custom_gate_names_are_normalized_to_the_device_spelling():
	from qiskit.circuit import Gate
	circuit = QuantumCircuit(1, 1)
	circuit.append(Gate("GPI", 1, [0.5]), [0])
	circuit.measure(0, 0)
	program, _measurement = to_braket_qasm3(circuit, _backend({"gpi"}))
	assert "gpi(0.5) q[0];" in program


def test_the_last_classical_write_wins_after_a_repeated_qubit():
	measurement = {"num_clbits": 2, "map": [(0, 0), (1, 0), (0, 1)]}
	assert remap_counts({"01": 10}, measurement) == {"10": 10}


@pytest.mark.parametrize("native", [None, RESTRICTED])
def test_the_bit_assignment_does_not_change_the_rows_a_backend_returns(native):
	# Measured out of order into a wider register, with idle qubits dropped:
	# qubit 3 lands in bit 1 and qubit 1 in bit 3. The program keeps only the
	# two measured qubits, as q[0] (was 1) and q[1] (was 3).
	circuit = QuantumCircuit(4, 4)
	circuit.x(3)
	circuit.measure(3, 1)
	circuit.measure(1, 3)

	program, measurement = to_braket_qasm3(circuit, _backend(BROAD, native=native))

	assert "qubit[2] q;" in program
	assert "bit[2] c;" in program
	assert _measure_lines(program) == [
		"c[0] = measure q[0];", "c[1] = measure q[1];"]
	assert measurement["num_clbits"] == 4
	assert sorted(measurement["map"]) == [(0, 3), (1, 1)]
	# The ideal shot has q[1] = 1. SV1 and Rigetti return the same row, the
	# library keys it "10", and that is bit 1 set in a 4-bit register.
	assert _library_keys(program, {0: 0, 1: 1}) == ("10", "10")
	assert remap_counts({"10": 10}, measurement) == {"0010": 10}


def test_a_qubit_measured_into_two_bits_is_measured_once_and_fills_both():
	circuit = QuantumCircuit(1, 2)
	circuit.x(0)
	circuit.measure(0, 0)
	circuit.measure(0, 1)

	program, measurement = to_braket_qasm3(circuit, _backend(BROAD))

	assert "bit[1] c;" in program
	assert _measure_lines(program) == ["c[0] = measure q[0];"]
	assert measurement == {"num_clbits": 2, "map": [(0, 0), (0, 1)]}
	assert remap_counts({"1": 10}, measurement) == {"11": 10}


def test_a_gate_after_a_measurement_is_rejected():
	# Braket takes terminal measurements only, and the library reports final
	# qubit values, so an earlier measurement could not be recovered anyway.
	circuit = QuantumCircuit(2, 2)
	circuit.measure(0, 0)
	circuit.x(0)
	circuit.measure(0, 1)

	with pytest.raises(DEFwExecutionError, match="terminal measurements"):
		to_braket_qasm3(circuit, _backend(BROAD))


def test_barriers_and_other_qubits_may_follow_a_measurement():
	circuit = QuantumCircuit(2, 2)
	circuit.measure(0, 0)
	circuit.barrier()
	circuit.h(1)
	circuit.measure(1, 1)

	program, measurement = to_braket_qasm3(circuit, _backend(BROAD))

	assert "h q[1];" in program
	assert _measure_lines(program) == [
		"c[0] = measure q[0];", "c[1] = measure q[1];"]
	assert sorted(measurement["map"]) == [(0, 0), (1, 1)]
