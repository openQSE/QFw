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


def compile_job(path, device_id, calibration_set_id):
	spec = importlib.util.spec_from_file_location(
		"qfw_metadata", REPO_ROOT / "backends/qfw_qiskit/qfw_metadata.py")
	metadata = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(metadata)

	circuit = QuantumCircuit(3, 3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)
	circuit.measure([0, 1, 2], [0, 1, 2])
	compiled = transpile(
		circuit, target=GenericBackendV2(3, seed=1).target, seed_transpiler=1)
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


def run_job(path, config_dir):
	import pytest
	import tests.mock.conftest  # noqa: F401
	import qfw_qiskit.qfw_job as qfw_job
	from defw_exception import DEFwExecutionError
	from svc_fake_iqm_qpm.svc_qpm import QPM
	from tests.mock import test_fake_iqm_qpm as fake_iqm
	from tests.mock.fakes import (
		FakeEventAPI, FakeQPM, FakeSchedulerContext, FakeSlurmDriver)
	from util.circuit_payload import qiskit_input

	monkeypatch = pytest.MonkeyPatch()
	fake_iqm._setup(monkeypatch)
	fake_iqm.configure_fake_credentials(
		monkeypatch, pathlib.Path(config_dir), "isa-user")
	qpm = QPM(
		admission_context_factory=fake_iqm.FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext)

	with open(path, "rb") as stream:
		compiled = qpy.load(stream)[0]
	client = FakeQPM(cids=["cid-isa"])
	client.qpm_properties = qpm.query()["properties"]
	backend = type("Backend", (), {"returns_statevector": lambda self: False})()
	reservation_id = _reserve(qpm)
	options = dict(
		FakeSlurmDriver().execution_options(shots=64),
		reservation_id=reservation_id)
	qfw_job.QFwJob(backend, client, FakeEventAPI(), compiled, options).submit()
	info = {
		key: value for key, value in client.submitted_payloads[0].items()
		if key not in ("reservation_id", "token", "timeout", "cancel_on_timeout")}

	try:
		response = qpm.async_run(info, reservation_id=reservation_id)
	except DEFwExecutionError as exc:
		return {"refused": str(exc), "cids": list(qpm.circuits)}
	received = qpm.circuits[response["cid"]].info
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
		print(json.dumps(run_job(*sys.argv[2:])))
