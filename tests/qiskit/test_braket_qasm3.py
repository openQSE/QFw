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


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

if "defw_exception" not in sys.modules:
	try:
		import defw_exception  # noqa: F401
	except ImportError:
		# Outside a QFw install there is no DEFw. The transcoder only needs
		# its exception type, so stand in a minimal one.
		stub = types.ModuleType("defw_exception")

		class DEFwExecutionError(Exception):
			pass

		stub.DEFwExecutionError = DEFwExecutionError
		sys.modules["defw_exception"] = stub

from defw_exception import DEFwExecutionError  # noqa: E402
from util import braket_transcode  # noqa: E402
from util.braket_transcode import remap_counts, to_braket_qasm3  # noqa: E402


# A Cepheus-like vocabulary: native rx, ry, cz plus Braket's broader set.
BROAD = {"rx", "ry", "cz", "rz", "h", "cnot", "x", "y", "z", "s", "si", "t",
	"ti", "v", "vi", "swap", "phaseshift", "cphaseshift"}
RESTRICTED = {"rx", "ry", "cz"}


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


def test_a_broad_vocabulary_only_renames():
	program, measurement = to_braket_qasm3(_ghz(), BROAD)

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
	program, measurement = to_braket_qasm3(_ghz(), RESTRICTED)

	assert _gate_names(program) <= RESTRICTED
	assert "cz" in _gate_names(program)
	assert "c[0] = measure q[0];" in program
	assert "barrier q[0], q[1], q[2];" in program
	assert measurement["num_clbits"] == 3
	assert sorted(measurement["map"]) == [(0, 0), (1, 1), (2, 2)]


def test_openqasm2_input_is_read_through_the_shared_loader():
	from util.iqm_transcode import load_qiskit_circuit
	circuit = load_qiskit_circuit(qasm2.dumps(_ghz()))

	program, _measurement = to_braket_qasm3(circuit, BROAD)

	assert "cnot q[0], q[1];" in program


def test_an_unrepresentable_circuit_fails_before_submission():
	circuit = QuantumCircuit(3)
	circuit.ccx(0, 1, 2)
	circuit.measure_all()

	# A device with no two-qubit gate cannot take a Toffoli.
	with pytest.raises(DEFwExecutionError, match="operation set"):
		to_braket_qasm3(circuit, {"rx"})


def test_braket_only_names_pass_when_the_device_accepts_them():
	# A circuit already in a device's own vocabulary (an IonQ-like set) is
	# passed through by name, with no decomposition attempted.
	from qiskit.circuit import Gate
	circuit = QuantumCircuit(2, 2)
	circuit.append(Gate("gpi", 1, [0.5]), [0])
	circuit.append(Gate("ms", 2, [0.0, 0.0, 0.25]), [0, 1])
	circuit.measure([0, 1], [0, 1])

	program, _measurement = to_braket_qasm3(circuit, {"gpi", "gpi2", "ms"})

	assert "gpi(0.5) q[0];" in program
	# Qiskit's exporter prints a zero parameter as 0.
	assert "ms(0, 0, 0.25) q[0], q[1];" in program


def test_idle_qubits_are_dropped_and_the_map_follows():
	circuit = QuantumCircuit(3, 1)
	circuit.x(2)
	circuit.measure(2, 0)

	program, measurement = to_braket_qasm3(circuit, BROAD)

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

	program, measurement = to_braket_qasm3(circuit, BROAD)

	assert "c[0] = measure q[2];" in program
	assert "c[2] = measure q[0];" in program
	# The DAG round trip may reorder independent measurements; the remap
	# reads the map as a set of (qubit, bit) pairs, so order is immaterial.
	assert measurement["num_clbits"] == 3
	assert sorted(measurement["map"]) == [(0, 2), (1, 1), (2, 0)]
	assert remap_counts({"001": 10}, measurement) == {"100": 10}


def test_the_rename_table_round_trips():
	for qiskit_name, braket_name in braket_transcode.QISKIT_TO_BRAKET.items():
		assert braket_transcode.BRAKET_TO_QISKIT[braket_name] == qiskit_name
