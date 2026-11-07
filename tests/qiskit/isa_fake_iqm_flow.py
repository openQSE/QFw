# The two jobs of a compile-then-run flow, each in its own process, driven by
# test_isa_circuits.py: `compile` has no DEFw, and `run` needs the DEFw stubs the
# mock suite installs, which would replace real modules in a pytest process.

import importlib.util
import json
import pathlib
import sys

import numpy  # noqa: F401
import yaml  # noqa: F401
from qiskit import QuantumCircuit, qpy, transpile
from qiskit.providers.fake_provider import GenericBackendV2

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in (".", "services", "backends"):
	sys.path.insert(0, str(REPO_ROOT / path))


IQM_CALIBRATION_SET_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def compile_job(path, device_id, calibration_set_id):
	spec = importlib.util.spec_from_file_location(
		"qfw_metadata", REPO_ROOT / "backends/qfw_qiskit/qfw_metadata.py")
	metadata = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(metadata)

	compiled = transpile(
		_ghz(), target=GenericBackendV2(3, seed=1).target, seed_transpiler=1)
	metadata.mark_isa(
		compiled, "fake-iqm", device_id,
		calibration={"kind": "set-id", "value": calibration_set_id},
		qubit_labels=["QB1", "QB2", "QB3"])
	with open(path, "wb") as stream:
		qpy.dump(compiled, stream)


def _reserve(qpm):
	return qpm.reserve(request={
		"owner": {"user": "isa-user"},
		"job_id": "job-isa",
		"scope_id": "allocation-1",
		"target_device_id": "fake-iqm-20q",
		"walltime_ns": 1_000_000_000,
		"task_class": {
			"qubit_count": 3,
			"depth": 12,
			"one_q_gate_count": 20,
			"two_q_gate_count": 6,
			"measurement_count": 3,
			"shots": 64,
		},
	})["reservation_id"]


def _ghz():
	circuit = QuantumCircuit(3, 3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)
	circuit.measure([0, 1, 2], [0, 1, 2])
	return circuit


def _fake_iqm_qpm(monkeypatch, config_dir):
	from svc_fake_iqm_qpm.svc_qpm import QPM
	from tests.mock import test_fake_iqm_qpm as fake_iqm
	from tests.mock.fakes import FakeSchedulerContext

	fake_iqm._setup(monkeypatch)
	fake_iqm.configure_fake_credentials(
		monkeypatch, pathlib.Path(config_dir), "isa-user")
	return QPM(
		admission_context_factory=fake_iqm.FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext)


def backend_compile_job(path, config_dir):
	import pytest
	import tests.mock.conftest  # noqa: F401
	from tests.mock import test_qfw_backend_target as target_tests

	monkeypatch = pytest.MonkeyPatch()
	qpm = _fake_iqm_qpm(monkeypatch, config_dir)
	backend = target_tests._backend(
		monkeypatch, qpm, qpm_properties=qpm.query()["properties"])
	compiled = backend.compile(_ghz(), seed_transpiler=1)
	with open(path, "wb") as stream:
		qpy.dump(compiled, stream)
	return {"target_kind": backend.target_kind}


def snapshot_job(path, config_dir):
	import pytest
	import tests.mock.conftest  # noqa: F401
	from tests.mock import test_qfw_backend_target as target_tests

	monkeypatch = pytest.MonkeyPatch()
	qpm = _fake_iqm_qpm(monkeypatch, config_dir)
	backend = target_tests._backend(
		monkeypatch, qpm, qpm_properties=qpm.query()["properties"])
	with open(path, "w") as stream:
		json.dump(backend.device_snapshot(), stream)
	return {"written": True}


def snapshot_compile_job(snapshot_path, path):
	sys.modules["defw_exception"] = None
	from qfw_qiskit.qfw_compile import SnapshotDevice

	compiled = SnapshotDevice.load(snapshot_path).compile(
		_ghz(), seed_transpiler=1)
	with open(path, "wb") as stream:
		qpy.dump(compiled, stream)
	return {"defw_loaded": "defw" in sys.modules}


def check_job(path, config_dir):
	import pytest
	import tests.mock.conftest  # noqa: F401
	from defw_exception import DEFwExecutionError
	from tests.mock import test_qfw_backend_target as target_tests

	monkeypatch = pytest.MonkeyPatch()
	qpm = _fake_iqm_qpm(monkeypatch, config_dir)
	backend = target_tests._backend(
		monkeypatch, qpm, qpm_properties=qpm.query()["properties"])
	with open(path, "rb") as stream:
		compiled = qpy.load(stream)[0]
	try:
		backend.check_isa(compiled)
	except DEFwExecutionError as exc:
		return {"refused": str(exc)}
	return {"checked": True}


def iqm_job(fake_backend):
	import pytest
	from iqm.qiskit_iqm import fake_backends
	from iqm.qiskit_iqm.fake_backends.fake_garnet import IQMFakeGarnet
	import tests.mock.conftest  # noqa: F401
	import util.iqm_transcode as iqm_transcode
	from qfw_qiskit import qfw_metadata
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	from tests.mock import test_qfw_backend_target as target_tests
	from types import SimpleNamespace
	import util.circuit_payload as circuit_payload

	monkeypatch = pytest.MonkeyPatch()
	make = {"garnet": IQMFakeGarnet, "deneb": fake_backends.IQMFakeDeneb}
	dynamic = iqm_transcode.to_jsonable(make[fake_backend]().architecture)
	dynamic["calibration_set_id"] = IQM_CALIBRATION_SET_ID
	telemetry = target_tests.FakeTelemetryAPI(
		backend_info={"backend": "iqm", "metadata_supported": True})
	telemetry.records["get_dynamic_backend_info"] = {
		"dynamic_architecture": dynamic}
	properties = {
		"isa_provider": "iqm", "isa_device_id": "q20",
		"circuit_stages": ["generic", "isa"], "dynamic_architecture": True}
	backend = target_tests._backend(
		monkeypatch, telemetry, qpm_properties=properties)
	compiled = backend.compile(_ghz())
	backend.check_isa(compiled)

	marker = qfw_metadata.get_isa_marker(compiled)
	encoded = circuit_payload.encode_qiskit_circuit(
		compiled, circuit_payload.qiskit_circuit_formats())
	assert encoded["circuit"]["format"] in circuit_payload.QPY_FORMATS
	built = []

	class Stop(Exception):
		pass

	def stop(iqm_circuit, shots, calibration_set_id):
		built.append((iqm_circuit, calibration_set_id))
		raise Stop()

	driver = QrmiDriver({"provider": "iqm"})
	driver._resource = lambda: object()
	driver._read_target = lambda credential=None: {
		"dynamic_quantum_architecture": dynamic}
	driver._build_iqmjson = stop
	source = SimpleNamespace(
		info=dict(encoded, isa=marker), provider_credential=None)
	try:
		driver._run_iqm_circuit(source)
	except Stop:
		pass
	return {
		"labels": marker["qubit_labels"],
		"calibration": marker["calibration"],
		"resonators": dynamic.get("computational_resonators") or [],
		"operations": sorted({
			operation.name for operation in built[0][0].instructions}),
		"pinned": built[0][1],
	}


def run_job(path, config_dir):
	import pytest
	import tests.mock.conftest  # noqa: F401
	import qfw_qiskit.qfw_job as qfw_job
	from defw_exception import DEFwExecutionError
	from tests.mock import test_fake_iqm_qpm as fake_iqm
	from tests.mock.fakes import FakeEventAPI, FakeQPM, FakeSlurmDriver
	from util.circuit_payload import qiskit_input

	qpm = _fake_iqm_qpm(pytest.MonkeyPatch(), config_dir)

	with open(path, "rb") as stream:
		compiled = qpy.load(stream)[0]
	client = FakeQPM(cids=["cid-isa"])
	client.qpm_properties = qpm.query()["properties"]
	backend = type("Backend", (), {"returns_statevector": lambda self: False})()
	reservation_id = _reserve(qpm)
	options = dict(
		FakeSlurmDriver().execution_options(shots=64),
		reservation_id=reservation_id)
	# The QPM drops a circuit once it has run, so read what it registered
	# before that.
	registered = {}
	create_circuit = qpm.create_circuit

	def recording_create_circuit(info, request=None):
		cid = create_circuit(info, request=request)
		registered[cid] = qpm.circuits[cid].info
		return cid

	qpm.create_circuit = recording_create_circuit
	try:
		qfw_job.QFwJob(
			backend, client, FakeEventAPI(), compiled, options).submit()
		info = {
			key: value for key, value in client.submitted_payloads[0].items()
			if key not in (
				"reservation_id", "token", "timeout", "cancel_on_timeout")}
		response = qpm.async_run(info, reservation_id=reservation_id)
	except DEFwExecutionError as exc:
		return {"refused": str(exc), "cids": list(qpm.circuits)}
	received = registered[response["cid"]]
	completion = fake_iqm._wait_for_completion(
		qpm, response["cid"], reservation_id)
	return {
		"outcome": completion["outcome"],
		"device_id": received["isa"]["device_id"],
		"unchanged": qiskit_input(received) == compiled,
	}


if __name__ == "__main__":
	if sys.argv[1] == "compile":
		compile_job(*sys.argv[2:])
	else:
		jobs = {
			"backend-compile": backend_compile_job,
			"check": check_job,
			"iqm": iqm_job,
			"run": run_job,
			"snapshot": snapshot_job,
			"snapshot-compile": snapshot_compile_job,
		}
		print(json.dumps(jobs[sys.argv[1]](*sys.argv[2:])))
