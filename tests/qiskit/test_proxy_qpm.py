# The proxy QPM (svc_proxy_qpm) end to end in one process: selection, directory
# properties, the telemetry record, and jobs through the QPM's own async_run
# with a stand-in admission and scheduler. Needs real numpy, Qiskit and Aer.
#
# The service modules import DEFw, which the mock tier's conftest stubs. It is
# imported after the real numpy and Qiskit, so it leaves them alone.

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
from qfw_qiskit.qfw_job import LOGICAL_CIRCUIT_METADATA_KEY, QFwJob  # noqa: E402
from qfw_qiskit.qfw_target import build_device_target  # noqa: E402
from qfw_qiskit.qpm_selection import qpm_selection_for_provider  # noqa: E402
from api_qpm_common import QPMType  # noqa: E402
from svc_proxy_qpm.svc_qpm import QPM  # noqa: E402
from util import circuit_payload  # noqa: E402
from util.qpm.controller import _clear_target_controllers_for_tests  # noqa: E402

DEVICE = "FakeFez"


@pytest.fixture
def qpm(monkeypatch):
	_clear_target_controllers_for_tests()
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")
	monkeypatch.setenv("QFW_QPU_DEVICE_ID", DEVICE)
	monkeypatch.setenv("QFW_PROXY_MAX_MEMORY_MB", "8")
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	return QPM(
		admission_context_factory=FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext,
		backend=FakeFez())


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
		assert completion["outcome"] == "COMPLETED"
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


def test_a_logical_circuit_travels_beside_the_compiled_one(qpm):
	logical = QuantumCircuit(3, 3)
	logical.x(0)
	logical.measure(range(3), range(3))
	isa = transpile(logical, FakeFez(), initial_layout=[4, 5, 6], seed_transpiler=1)
	properties = qpm.query()["properties"]
	info = _job_info(qpm, isa, **circuit_payload.encode_logical_circuit(logical, properties))

	assert set(info["logical_circuit"]) == {"format", "data"}
	assert circuit_payload.logical_circuit_payload(info)[0] == circuit_payload.QPY
	completion = _run(qpm, info)

	assert completion["outcome"] == "COMPLETED"
	# The logical circuit is an X on one qubit: its ideal outcome is 001, up to readout error.
	counts = completion["result"]["counts"]
	assert max(counts, key=counts.get) == "001"


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


def test_a_job_without_a_logical_circuit_reads_none():
	assert circuit_payload.logical_circuit_payload({"qasm": "OPENQASM 2.0;"}) is None


def test_a_bases_job_returns_one_record_per_basis(qpm):
	prepared = QuantumCircuit(2)
	prepared.h(0)
	prepared.cx(0, 1)
	isa = transpile(prepared, FakeFez(), initial_layout=[0, 1], seed_transpiler=1)
	bases = {"physical": [0, 1], "bases": ["ZZ", "XX"], "shots": [300, 500]}
	completion = _run(qpm, _job_info(qpm, isa, bases=bases))

	assert completion["outcome"] == "COMPLETED"
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


def test_an_unknown_emulated_device_is_named():
	from util.proxy_sampler.device import load_emulated_device

	with pytest.raises(ValueError, match="no device 'FakeNowhere'"):
		load_emulated_device("FakeNowhere")


def test_the_seed_fixes_the_ideal_and_noise_draws_together():
	from svc_proxy_qpm.svc_qrc import seeded_generators

	ideal, noise = seeded_generators(3)
	again_ideal, again_noise = seeded_generators(3)
	assert ideal.random() == again_ideal.random()
	assert noise.random() == again_noise.random()
	assert not np.array_equal(seeded_generators(3)[0].random(4), seeded_generators(3)[1].random(4))
