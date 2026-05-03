import pathlib
import sys
import importlib.util

from qiskit import QuantumCircuit, transpile


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET_MODULE = REPO_ROOT / "backends" / "qfw_qiskit" / "qfw_target.py"


spec = importlib.util.spec_from_file_location("qfw_target", TARGET_MODULE)
qfw_target = importlib.util.module_from_spec(spec)
sys.modules["qfw_target"] = qfw_target
spec.loader.exec_module(qfw_target)


def test_qfw_target_transpiles_pennylane_remote_circuit_shape():
	target = qfw_target.build_qfw_target(num_qubits=400)
	circuit = QuantumCircuit(2)
	circuit.rz(0.3, 0)
	circuit.ry(0.1, 0)
	circuit.rx(0.2, 0)
	circuit.cx(0, 1)

	transpiled = transpile(circuit, target=target)

	assert transpiled.num_qubits == 2
