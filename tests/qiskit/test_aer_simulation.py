import importlib.util
import json
import math
import pathlib
import subprocess
import sys

import pytest
from qiskit import QuantumCircuit, qasm2, transpile

pytest.importorskip("qiskit_aer")
fake_provider = pytest.importorskip("qiskit_ibm_runtime.fake_provider")
from qiskit_aer import AerSimulator  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICE_DIR = REPO_ROOT / "services" / "svc_aer_qpm"
SHOTS = 10_000
NOISY_FEZ = {"mode": "noisy", "device_id": "FakeFez"}


def _load(name, path):
	spec = importlib.util.spec_from_file_location(name, path)
	module = importlib.util.module_from_spec(spec)
	sys.modules[name] = module
	spec.loader.exec_module(module)
	return module


qfw_target = _load("qfw_target", REPO_ROOT / "backends" / "qfw_qiskit" / "qfw_target.py")
sys.path.insert(0, str(SERVICE_DIR))
import circuit_runner  # noqa: E402
import device  # noqa: E402


@pytest.fixture(scope="module")
def fez():
	return fake_provider.FakeFez()


@pytest.fixture(scope="module")
def bell_on_fez(fez):
	circuit = QuantumCircuit(2, 2)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.measure([0, 1], [0, 1])
	return transpile(
		circuit, target=fez.target, initial_layout=[0, 1], seed_transpiler=1)


def _odd_parity(counts):
	return (counts.get("01", 0) + counts.get("10", 0)) / sum(counts.values())


def test_target_from_telemetry_is_the_fake_backends_target(fez):
	configuration, properties = device.ibm_device_records("FakeFez")
	telemetry = {
		"backend": "fez-noisy", "metadata_supported": True,
		"static_architecture": configuration, "backend_properties": properties}

	target = qfw_target.build_device_target(telemetry)

	assert target.num_qubits == fez.target.num_qubits
	assert sorted(target.build_coupling_map().get_edges()) == sorted(
		fez.target.build_coupling_map().get_edges())
	assert set(target.operation_names) == set(fez.target.operation_names)
	for qargs in [(0, 1), (1, 2)]:
		assert target["cz"][qargs].error == fez.target["cz"][qargs].error
	assert target["sx"][(0,)].error == fez.target["sx"][(0,)].error
	assert target.qubit_properties[0].t1 == fez.target.qubit_properties[0].t1


def test_the_same_seed_gives_the_same_counts(bell_on_fez):
	qasm = qasm2.dumps(bell_on_fez)

	first = circuit_runner.sample_counts(qasm, SHOTS, seed=5, **NOISY_FEZ)
	again = circuit_runner.sample_counts(qasm, SHOTS, seed=5, **NOISY_FEZ)
	other = circuit_runner.sample_counts(qasm, SHOTS, seed=6, **NOISY_FEZ)

	assert first == again
	assert first != other


def test_bell_samples_carry_the_devices_noise(fez, bell_on_fez):
	qasm = qasm2.dumps(bell_on_fez)

	emulated = _odd_parity(
		circuit_runner.sample_counts(qasm, SHOTS, seed=11, **NOISY_FEZ))
	reference = _odd_parity(
		AerSimulator.from_backend(fez).run(
			bell_on_fez, shots=SHOTS, seed_simulator=12).result().get_counts())

	sigma_of_two_independent_samples = math.sqrt(
		2 * reference * (1 - reference) / SHOTS)
	assert abs(emulated - reference) < 3 * sigma_of_two_independent_samples
	assert emulated > 0
	assert emulated > 3 * math.sqrt(emulated * (1 - emulated) / SHOTS)


def test_a_circuit_too_large_for_the_memory_limit_fails_in_aer(fez):
	circuit = QuantumCircuit(30, 30)
	circuit.h(range(30))
	circuit.measure(range(30), range(30))

	with pytest.raises(Exception, match="(?i)memory"):
		circuit_runner.sample_counts(
			qasm2.dumps(transpile(circuit, target=fez.target, seed_transpiler=1)),
			10, method="statevector", max_memory_mb=1, **NOISY_FEZ)


def test_ideal_mode_samples_without_noise(bell_on_fez):
	counts = circuit_runner.sample_counts(
		qasm2.dumps(bell_on_fez), SHOTS, seed=3, mode="ideal")

	assert set(counts) <= {"00", "11"}
	assert _odd_parity(counts) == 0


def test_a_circuit_aer_refuses_fails_with_the_reason_first(tmp_path):
	circuit = QuantumCircuit(2, 2)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.measure([0, 1], [0, 1])
	qasm = tmp_path / "ghz.qasm"
	qasm.write_text(qasm2.dumps(circuit))

	run = subprocess.run(
		[sys.executable, str(SERVICE_DIR / "circuit_runner.py"),
		 "--qasm-file", str(qasm), "--shots", "10",
		 "--mode", "noisy", "--device-id", "FakeFez"],
		capture_output=True, text=True, cwd=SERVICE_DIR)

	assert run.returncode == 1
	assert run.stdout.splitlines()[0] == "aer: AerError: 'unknown instruction: h'"


@pytest.mark.skipif(
	"GPU" in AerSimulator().available_devices(), reason="this host has a GPU")
def test_a_gpu_the_host_lacks_fails_rather_than_runs_on_the_cpu(bell_on_fez):
	with pytest.raises(Exception, match="GPU"):
		circuit_runner.sample_counts(
			qasm2.dumps(bell_on_fez), 10, mode="ideal", device="GPU")


def _ghz(width):
	circuit = QuantumCircuit(width)
	circuit.h(0)
	for qubit in range(width - 1):
		circuit.cx(qubit, qubit + 1)
	circuit.measure_all()
	return qasm2.dumps(circuit)


def test_the_method_setting_reaches_aer():
	sim = circuit_runner.aer_simulator(method="matrix_product_state")

	assert sim.options.method == "matrix_product_state"


def test_the_mps_settings_reach_aer():
	sim = circuit_runner.aer_simulator(
		method="matrix_product_state", mps_max_bond_dimension=64,
		mps_truncation_threshold=1e-10)

	assert sim.options.matrix_product_state_max_bond_dimension == 64
	assert sim.options.matrix_product_state_truncation_threshold == 1e-10


def test_a_bond_dimension_of_one_keeps_one_branch_of_a_ghz_state(
		tmp_path, monkeypatch, capsys):
	qasm_file = tmp_path / "ghz.qasm"
	qasm_file.write_text(_ghz(4))
	monkeypatch.setattr(sys, "argv", [
		"circuit_runner", "--qasm-file", str(qasm_file), "--shots", "200",
		"--seed", "1", "--method", "matrix_product_state",
		"--mps-max-bond-dimension", "1"])

	circuit_runner.main()

	assert json.loads(capsys.readouterr().out)["counts"] == {"0000": 200}


def test_the_runner_returns_the_mps_truncation_when_asked(
		tmp_path, monkeypatch, capsys):
	qasm_file = tmp_path / "ghz.qasm"
	qasm_file.write_text(_ghz(4))
	monkeypatch.setattr(sys, "argv", [
		"circuit_runner", "--qasm-file", str(qasm_file), "--shots", "10",
		"--method", "matrix_product_state", "--mps-max-bond-dimension", "1",
		"--mps-log-data"])

	circuit_runner.main()

	diagnostics = json.loads(capsys.readouterr().out)["diagnostics"]
	assert "discarded_value=0.5" in diagnostics["MPS_log_data"]
	assert diagnostics["matrix_product_state_max_bond_dimension"] == 1


def test_matrix_product_state_runs_a_circuit_too_wide_for_statevector():
	qasm = _ghz(44)
	with pytest.raises(Exception, match="(?i)memory|insufficient"):
		circuit_runner.sample_counts(
			qasm, 10, mode="ideal", method="statevector", max_memory_mb=1024)

	counts = circuit_runner.sample_counts(
		qasm, 100, seed=1, mode="ideal", method="matrix_product_state",
		max_memory_mb=1024)

	assert set(counts) <= {"0" * 44, "1" * 44}
	assert sum(counts.values()) == 100


def test_an_ideal_runner_transpiles_gates_its_method_lacks():
	circuit = QuantumCircuit(2, 2)
	circuit.ry(0.4, 0)
	circuit.crx(0.7, 0, 1)
	circuit.cs(0, 1)
	circuit.ch(1, 0)
	circuit.measure([0, 1], [0, 1])

	counts = circuit_runner.sample_counts(
		qasm2.dumps(circuit), 100, method="matrix_product_state")

	assert sum(counts.values()) == 100
