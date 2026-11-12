import json
import os
import shlex
import subprocess
import sys
import threading
import time

import numpy as np
import pytest

pytest.importorskip("qiskit_aer")
pytest.importorskip("qiskit_ibm_runtime")

from qiskit import QuantumCircuit, transpile  # noqa: E402
from qiskit_ibm_runtime.fake_provider import FakeFez  # noqa: E402

from tests.mock import conftest as _defw_stubs  # noqa: E402,F401
from tests.mock.fakes import FakeSchedulerContext  # noqa: E402
from tests.mock.test_fake_iqm_qpm import FakeAdmissionContext  # noqa: E402

import util.qpm.util_qpm as util_qpm  # noqa: E402
import util.qpm.util_qrc as util_qrc  # noqa: E402
import svc_proxy_qpm.svc_qrc as proxy_qrc  # noqa: E402
from defw_exception import DEFwInProgress  # noqa: E402
from svc_fake_iqm_qpm.svc_qrc import _CompletionEvent  # noqa: E402
from qfw_qiskit.qfw_job import LOGICAL_CIRCUIT_METADATA_KEY, QFwJob  # noqa: E402
from qfw_qiskit.qfw_target import build_device_target  # noqa: E402
from qfw_qiskit.qpm_selection import qpm_selection_for_provider  # noqa: E402
from api_qpm_common import QPMType  # noqa: E402
from svc_proxy_qpm.svc_qpm import QPM  # noqa: E402
from util import circuit_payload  # noqa: E402
from util.qpm.controller import _clear_target_controllers_for_tests  # noqa: E402

DEVICE = "FakeFez"


SERVICE = "proxy-fez"
RUNNER_SLOTS = 4


class SubprocessLauncher:
	def __init__(self):
		self.processes = {}
		self.finished = {}
		self.commands = []

	def launch(self, cmd):
		self.commands.append(shlex.split(cmd))
		process = subprocess.Popen(
			shlex.split(cmd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
		self.processes[process.pid] = process

		def wait():
			stdout, stderr = process.communicate()
			self.finished[process.pid] = (stdout, stderr, process.returncode)

		threading.Thread(target=wait, daemon=True).start()
		return process.pid

	def status(self, pid):
		if pid not in self.finished:
			raise DEFwInProgress(f"runner {pid} is running")
		return self.finished.pop(pid)

	def running(self):
		return [pid for pid, process in self.processes.items() if process.poll() is None]

	def shutdown(self):
		for process in self.processes.values():
			if process.poll() is None:
				process.kill()


def _plain_command(executable, executable_args=None, **mpi):
	return shlex.join([executable, *(str(arg) for arg in executable_args or ())])


@pytest.fixture(autouse=True)
def runners_as_local_processes(tmp_path, monkeypatch):
	monkeypatch.setattr(util_qrc.os, "sched_getaffinity", lambda pid: {0}, raising=False)
	monkeypatch.setattr(util_qrc.os, "sched_setaffinity", lambda pid, mask: None, raising=False)
	monkeypatch.setattr(util_qrc.svc_launcher, "Launcher", SubprocessLauncher)
	monkeypatch.setattr(util_qrc, "Event", _CompletionEvent)
	monkeypatch.setattr(proxy_qrc.cdefw_global, "get_defw_tmp_dir", lambda: str(tmp_path), raising=False)
	monkeypatch.setattr(proxy_qrc, "build_mpi_command_string", _plain_command)


@pytest.fixture
def service_entry(tmp_path, monkeypatch):
	def configure(circuit_time_limit_seconds=None, **proxy):
		entry = {"name": SERVICE, "module": "svc_proxy_qpm", "device-id": DEVICE}
		if proxy:
			entry["proxy"] = proxy
		if circuit_time_limit_seconds is not None:
			entry["circuit-time-limit-seconds"] = circuit_time_limit_seconds
		path = tmp_path / "services.yaml"
		path.write_text(json.dumps({"services": [entry]}))
		monkeypatch.setenv("QFW_SERVICE_CONFIG", str(path))
		monkeypatch.setenv("QFW_QPM_SERVICE_NAME", SERVICE)
	return configure


def _start_qpm(monkeypatch, backend=None):
	_clear_target_controllers_for_tests()
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", f"localhost:{RUNNER_SLOTS}")
	monkeypatch.setenv("QFW_QPU_DEVICE_ID", DEVICE)
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	return QPM(
		admission_context_factory=FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext,
		backend=backend or FakeFez())


@pytest.fixture
def qpm(service_entry, monkeypatch):
	service_entry(**{"max-memory-mb": 8})
	started = _start_qpm(monkeypatch)
	yield started
	started.qrc.shutdown()


def _ghz(width, physical):
	circuit = QuantumCircuit(width, width)
	circuit.h(0)
	for qubit in range(width - 1):
		circuit.cx(qubit, qubit + 1)
	circuit.measure(range(width), range(width))
	return transpile(circuit, FakeFez(), initial_layout=physical, seed_transpiler=1)


def _run(qpm, info):
	task_class = {
		"qubit_count": info["num_qubits"], "depth": 4, "one_q_gate_count": 4,
		"two_q_gate_count": 1, "measurement_count": 2, "shots": 16}
	decision = qpm.reserve(request={
		"owner": {"user": "proxy-user"}, "job_id": "job-proxy",
		"scope_id": "allocation-proxy", "target_device_id": DEVICE,
		"task_class": task_class})
	response = qpm.async_run(info, reservation_id=decision["reservation_id"])
	deadline = time.monotonic() + 30
	while time.monotonic() < deadline:
		completion = qpm.read_cq(cid=response["cid"], reservation_id=decision["reservation_id"])
		if completion.get("completion_ready"):
			return completion
		time.sleep(0.02)
	raise AssertionError("the proxy job never completed")


def _job_info(qpm, circuit, shots=200, seed=7, **extra):
	properties = qpm.query()["properties"]
	info = {"num_qubits": circuit.num_qubits, "num_shots": shots, "seed_simulator": seed}
	info.update(circuit_payload.encode_qiskit_circuit(circuit, properties))
	info.update(extra)
	return info


def test_the_proxy_provider_is_a_simulator():
	selection = qpm_selection_for_provider("proxy")
	assert selection["provider"] == "proxy"
	assert selection["qpm_type"] & QPMType.QPM_TYPE_SIMULATOR
	assert not selection["qpm_type"] & QPMType.QPM_TYPE_HARDWARE


def test_the_directory_record_names_the_device_and_what_the_qpm_reads(qpm):
	info = qpm.query()
	properties = info["properties"]

	assert info["service_id"] == f"qpm:proxy:{DEVICE}"
	assert properties["simulator"] is True and properties["hardware"] is False
	assert properties["num_qubits"] == 156
	assert properties["max_memory_mb"] == 8
	assert properties["logical_circuit"] is True and properties["bases_jobs"] is True
	assert properties["circuit_formats"][0] == circuit_payload.QPY_GZIP


def test_the_telemetry_record_builds_the_devices_target(qpm):
	backend_info = qpm.get_backend_info()
	target = build_device_target(backend_info)
	device = FakeFez().target

	assert backend_info["metadata_supported"] is True
	assert target.num_qubits == device.num_qubits
	assert sorted(target.build_coupling_map().get_edges()) == sorted(device.build_coupling_map().get_edges())
	edge = sorted(device["cz"])[0]
	assert target["cz"][edge].error == pytest.approx(device["cz"][edge].error)
	assert target.qubit_properties[0].t1 == pytest.approx(device.qubit_properties[0].t1)


def test_the_same_seed_returns_the_same_counts_and_another_seed_does_not(qpm):
	circuit = _ghz(4, [0, 1, 2, 3])

	def counts(seed):
		completion = _run(qpm, _job_info(qpm, circuit, seed=seed))
		assert completion["rc"] == 0
		return completion["result"]["counts"]

	first = counts(7)
	assert sum(first.values()) == 200
	assert counts(7) == first
	assert counts(8) != first


def test_the_job_returns_the_error_budget_as_diagnostics(qpm):
	completion = _run(qpm, _job_info(qpm, _ghz(3, [0, 1, 2])))
	diagnostics = completion["result"]["diagnostics"]

	assert diagnostics["tier"] == "proxy"
	assert diagnostics["ideal"] == "statevector"
	assert diagnostics["gates_2q"] == 2
	assert 0 < diagnostics["fidelity"] < 1


def _x_on(qubit):
	circuit = QuantumCircuit(3, 3)
	circuit.x(qubit)
	circuit.measure(range(3), range(3))
	return circuit


def test_the_ideal_outcomes_come_from_the_logical_circuit_sent_beside_the_compiled_one(qpm):
	isa = transpile(_x_on(0), FakeFez(), initial_layout=[4, 5, 6], seed_transpiler=1)
	properties = qpm.query()["properties"]
	info = _job_info(qpm, isa, **circuit_payload.encode_logical_circuit(_x_on(1), properties))

	assert set(info["logical_circuit"]) == {"format", "data"}
	assert circuit_payload.logical_circuit_payload(info)[0] == circuit_payload.QPY
	completion = _run(qpm, info)

	assert completion["rc"] == 0
	counts = completion["result"]["counts"]
	x_on_logical_qubit_one = "010"
	assert max(counts, key=counts.get) == x_on_logical_qubit_one
	assert completion["result"]["diagnostics"]["ideal"] == "pauli-rotations"


def test_the_client_sends_the_logical_circuit_from_the_circuit_metadata(qpm):
	logical = QuantumCircuit(2, 2)
	logical.x(0)
	logical.measure(range(2), range(2))
	isa = transpile(logical, FakeFez(), initial_layout=[0, 1], seed_transpiler=1)
	isa.metadata = {"keep": 1, LOGICAL_CIRCUIT_METADATA_KEY: logical}
	job = object.__new__(QFwJob)
	job._qpm = type("Directory", (), {"qpm_properties": qpm.query()["properties"]})()

	fields = job._encode_circuit(isa, logical)

	assert set(fields) == {"circuit", "logical_circuit"}
	sent = circuit_payload.load_circuit(*circuit_payload.circuit_payload(fields))
	assert sent.metadata == {"keep": 1}
	assert circuit_payload.load_circuit(*circuit_payload.logical_circuit_payload(fields)) == logical
	assert LOGICAL_CIRCUIT_METADATA_KEY in isa.metadata


def test_the_client_refuses_a_logical_circuit_for_a_qpm_that_does_not_read_one():
	job = object.__new__(QFwJob)
	job._qpm = type("Directory", (), {"qpm_properties": {"provider": "nwqsim"}})()
	circuit = QuantumCircuit(1)
	with pytest.raises(Exception, match="does not read one"):
		job._encode_circuit(circuit, QuantumCircuit(1))


def test_the_client_refuses_a_bases_job_for_a_qpm_that_does_not_run_one():
	job = object.__new__(QFwJob)
	job._qpm = type("Directory", (), {"qpm_properties": {"provider": "nwqsim"}})()
	job._qobj = QuantumCircuit(1)
	job._options = {"bases": {"physical": [0], "bases": ["Z"], "shots": [1]}}
	with pytest.raises(Exception, match="does not run bases jobs"):
		job.submit()


def test_a_job_without_a_logical_circuit_reads_none():
	assert circuit_payload.logical_circuit_payload({"qasm": "OPENQASM 2.0;"}) is None


def test_a_bases_job_returns_one_record_per_basis(qpm):
	prepared = QuantumCircuit(2)
	prepared.h(0)
	prepared.cx(0, 1)
	isa = transpile(prepared, FakeFez(), initial_layout=[0, 1], seed_transpiler=1)
	bases = {"physical": [0, 1], "bases": ["ZZ", "XX"], "shots": [300, 500]}
	completion = _run(qpm, _job_info(qpm, isa, bases=bases))

	assert completion["rc"] == 0
	records = completion["result"]["bases"]
	assert [(record["basis"], sum(record["counts"].values())) for record in records] == [("ZZ", 300), ("XX", 500)]
	assert records[0]["diagnostics"]["ideal"] == "statevector-bases"


def test_a_bases_job_beyond_the_memory_limit_fails_with_a_named_error(qpm):
	wide = QuantumCircuit(24)
	wide.h(0)
	for qubit in range(23):
		wide.cx(qubit, qubit + 1)
	isa = transpile(wide, FakeFez(), initial_layout=list(range(24)), seed_transpiler=1)
	bases = {"physical": [0, 1], "bases": ["ZZ"], "shots": [10]}
	completion = _run(qpm, _job_info(qpm, isa, bases=bases))

	assert completion["outcome"] == "FAILED"
	error = completion["result"]["proxy"]
	assert error["error_type"] == "AerMemoryRefused"
	assert "memory limit" in error["error"]


def test_an_untranspiled_circuit_fails_with_what_to_do(qpm):
	untranspiled = QuantumCircuit(2, 2)
	untranspiled.h(0)
	untranspiled.cx(0, 1)
	untranspiled.measure(range(2), range(2))
	completion = _run(qpm, _job_info(qpm, untranspiled))

	assert completion["outcome"] == "FAILED"
	error = completion["result"]["proxy"]
	assert error["error_type"] == "LookupError"
	assert error["error"].endswith("Transpile the circuit for backend.target")


def test_an_unknown_emulated_device_is_named_by_its_setting():
	from util.proxy_sampler.device import load_emulated_device

	with pytest.raises(ValueError, match="device-id 'FakeNowhere' is not a qiskit_ibm_runtime.fake_provider class"):
		load_emulated_device("FakeNowhere")


def test_without_a_proxy_block_the_memory_limit_is_the_default(service_entry, monkeypatch):
	from svc_proxy_qpm.settings import DEFAULT_MAX_MEMORY_MB

	service_entry()

	assert _start_qpm(monkeypatch).query()["properties"]["max_memory_mb"] == DEFAULT_MAX_MEMORY_MB


@pytest.mark.parametrize("proxy, refusal", [
	({"max-memory-mb": "4GB"}, "proxy max-memory-mb must be a positive whole number of MiB, not '4GB'"),
	({"max-memory-mb": 0}, "proxy max-memory-mb must be a positive whole number of MiB, not 0"),
	({"max-memory-mb": -5}, "proxy max-memory-mb must be a positive whole number of MiB, not -5"),
	({"max-memory-mb": 1.5}, "proxy max-memory-mb must be a positive whole number of MiB, not 1.5"),
	({"memory": 8}, "unknown proxy settings: memory"),
])
def test_a_proxy_block_it_cannot_use_stops_the_start_by_naming_the_setting(service_entry, monkeypatch, proxy, refusal):
	from defw_exception import DEFwExecutionError

	service_entry(**proxy)

	with pytest.raises(DEFwExecutionError, match=refusal):
		_start_qpm(monkeypatch)


def test_the_seed_fixes_the_ideal_and_noise_draws_together():
	from util.proxy_sampler.jobs import seeded_generators

	ideal, noise = seeded_generators(3)
	again_ideal, again_noise = seeded_generators(3)
	assert ideal.random() == again_ideal.random()
	assert noise.random() == again_noise.random()
	assert not np.array_equal(seeded_generators(3)[0].random(4), seeded_generators(3)[1].random(4))


def _lucj(norb, nelec, n_reps=1):
	ffsim = pytest.importorskip("ffsim")
	logical = QuantumCircuit(2 * norb)
	logical.append(ffsim.qiskit.PrepareHartreeFockJW(norb, nelec), range(2 * norb))
	if n_reps:
		pairs = ([(p, p + 1) for p in range(norb - 1)], [(p, p) for p in range(norb)])
		operator = ffsim.random.random_ucj_op_spin_balanced(
			norb, n_reps=n_reps, interaction_pairs=pairs, with_final_orbital_rotation=True, seed=1)
		logical.append(ffsim.qiskit.UCJOpSpinBalancedJW(operator), range(2 * norb))
	logical.measure_all()
	return logical


def _logical_job(qpm, logical, **extra):
	isa = transpile(logical, FakeFez(), initial_layout=list(range(logical.num_qubits)), seed_transpiler=1)
	properties = qpm.query()["properties"]
	return _job_info(qpm, isa, **circuit_payload.encode_logical_circuit(logical, properties), **extra)


def test_a_lucj_logical_circuit_sent_as_qpy_is_sampled_by_ffsim(qpm):
	completion = _run(qpm, _logical_job(qpm, _lucj(4, (2, 2))))

	assert completion["rc"] == 0
	assert completion["result"]["diagnostics"]["ideal"] == "ffsim"


def test_an_ffsim_state_sent_as_qpy_beyond_the_memory_limit_is_refused_by_name(qpm):
	completion = _run(qpm, _logical_job(qpm, _lucj(12, (6, 6), n_reps=0)))

	assert completion["outcome"] == "FAILED"
	error = completion["result"]["proxy"]
	assert error["error_type"] == "FfsimMemoryRefused"
	assert "12 orbitals and (6, 6) electrons" in error["error"]


SLOW_SHOTS = 1_000_000
RUNNER = os.path.join(os.path.dirname(proxy_qrc.__file__), "circuit_runner.py")


def _wait_for(check, timeout=60):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if check():
			return True
		time.sleep(0.02)
	return check()


def _reserve(qpm):
	return qpm.reserve(request={
		"owner": {"user": "proxy-user"}, "job_id": "job-proxy", "scope_id": "allocation-proxy",
		"target_device_id": DEVICE, "task_class": {"qubit_count": 20, "shots": 16}})["reservation_id"]


def _slow_job(qpm):
	ghz = QuantumCircuit(20, 20)
	ghz.h(0)
	for qubit in range(19):
		ghz.cx(qubit, qubit + 1)
	ghz.measure(range(20), range(20))
	return _job_info(qpm, transpile(ghz, FakeFez(), seed_transpiler=1, optimization_level=1), shots=SLOW_SHOTS)


def test_a_runner_that_outlives_the_service_entrys_time_limit_is_killed(service_entry, monkeypatch):
	service_entry(circuit_time_limit_seconds=1)
	qpm = _start_qpm(monkeypatch)
	try:
		completion = _run(qpm, _slow_job(qpm))
	finally:
		qpm.qrc.shutdown()

	assert completion["rc"] != 0
	assert "killed after 1 s" in completion["result"]
	assert "services[proxy-fez].circuit-time-limit-seconds" in completion["result"]


def test_cancelling_a_job_that_is_sampling_stops_its_runner(qpm):
	reservation = _reserve(qpm)
	cid = qpm.async_run(_slow_job(qpm), reservation_id=reservation)["cid"]
	assert _wait_for(lambda: qpm.qrc.launcher.running())

	qpm.cancel_task(cid=cid, reservation_id=reservation)

	assert _wait_for(lambda: not qpm.qrc.launcher.running(), timeout=10)
	completion = qpm.read_cq(cid=cid, reservation_id=reservation)
	assert completion.get("outcome") == "CANCELLED"
	assert completion.get("rc") != 0


def test_jobs_sample_in_parallel_runners(qpm):
	reservation = _reserve(qpm)
	for _ in range(2):
		qpm.async_run(_slow_job(qpm), reservation_id=reservation)

	assert _wait_for(lambda: len(qpm.qrc.launcher.running()) == 2)


def test_a_failed_runner_prints_its_reason_first(tmp_path):
	untranspiled = QuantumCircuit(1, 1)
	untranspiled.h(0)
	untranspiled.measure(0, 0)
	job = tmp_path / "job.json"
	job.write_text(json.dumps(proxy_qrc.runner_job(
		{"num_shots": 10, **circuit_payload.encode_qiskit_circuit(untranspiled, circuit_payload.qiskit_circuit_formats())})))

	runner = subprocess.run(
		[sys.executable, RUNNER, "--job-file", str(job), "--log-file", str(tmp_path / "log"),
		 "--device-id", DEVICE, "--max-memory-mb", "8"], capture_output=True, text=True)

	assert runner.returncode == 1
	assert runner.stdout.splitlines()[0].startswith("proxy: LookupError: the device reports no error for h")


def test_the_runner_samples_under_the_service_entrys_memory_limit_and_device(qpm):
	completion = _run(qpm, _logical_job(qpm, _lucj(12, (6, 6), n_reps=0)))

	assert completion["result"]["proxy"]["error_type"] == "FfsimMemoryRefused"
	command = qpm.qrc.launcher.commands[-1]
	assert command[command.index("--max-memory-mb") + 1] == "8"
	assert command[command.index("--device-id") + 1] == DEVICE
