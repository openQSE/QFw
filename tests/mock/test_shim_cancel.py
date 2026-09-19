# Guards cancelling a circuit the QRMI/QDMI shim is running. The QPM
# controller cancels through the shim QRC's cancel(), which sets the
# circuit's cancel_event. The driver notices while it polls and stops the
# provider job (QRMI task_stop, FoMaC job.cancel()). A timeout stops the job
# too, so nothing keeps running at the provider after QFw has given up on it.
#
# The provider side is stubbed, so no qiskit, QRMI or QDMI is needed.

import pathlib
import sys
import threading
import time
import types

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm import svc_qrc  # noqa: E402
from svc_lib_qpm.drivers import qdmi_driver  # noqa: E402
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402


def _cancelled():
	event = threading.Event()
	event.set()
	return event


# --- QRMI: stopping the provider job ----------------------------------------

class _QrmiResource:
	def __init__(self, statuses, stop_error=None):
		self.statuses = list(statuses)
		self.stop_error = stop_error
		self.started = []
		self.stopped = []

	def task_status(self, job_id):
		if len(self.statuses) > 1:
			return self.statuses.pop(0)
		return self.statuses[0]

	def task_start(self, payload):
		self.started.append(payload)
		return "job-1"

	def task_stop(self, job_id):
		self.stopped.append(job_id)
		if self.stop_error:
			raise RuntimeError(self.stop_error)


def _qrmi_driver(resource):
	driver = QrmiDriver({"provider": "iqm"})
	driver._qpu = lambda credential=None: resource
	return driver


def test_a_cancel_stops_the_qrmi_job():
	resource = _QrmiResource(["Running"])

	status = _qrmi_driver(resource)._poll_task(
		"job-1", 60, 5.0, cancel_event=_cancelled())

	assert status == "cancelled"
	assert resource.stopped == ["job-1"]


def test_a_cancel_is_noticed_without_waiting_out_the_poll():
	resource = _QrmiResource(["Running"])
	cancel = threading.Event()
	threading.Timer(0.05, cancel.set).start()
	started = time.monotonic()

	status = _qrmi_driver(resource)._poll_task(
		"job-1", 60, 30.0, cancel_event=cancel)

	assert status == "cancelled"
	assert time.monotonic() - started < 5.0
	assert resource.stopped == ["job-1"]


def test_a_timeout_stops_the_qrmi_job():
	resource = _QrmiResource(["Running"])

	with pytest.raises(DEFwExecutionError, match="and it was stopped"):
		_qrmi_driver(resource)._poll_task("job-1", 0.0, 0.0)

	assert resource.stopped == ["job-1"]


def test_a_failed_stop_is_reported_with_the_timeout():
	resource = _QrmiResource(["Running"], stop_error="provider unreachable")

	with pytest.raises(
			DEFwExecutionError,
			match="task_stop failed: provider unreachable"):
		_qrmi_driver(resource)._poll_task("job-1", 0.0, 0.0)


def test_a_finished_qrmi_job_is_not_stopped():
	resource = _QrmiResource(["Completed"])

	status = _qrmi_driver(resource)._poll_task(
		"job-1", 60, 0.0, cancel_event=threading.Event())

	assert status == "completed"
	assert resource.stopped == []


def test_a_cancel_before_submission_starts_no_qrmi_job(monkeypatch):
	import util.iqm_transcode as iqm_transcode
	monkeypatch.setattr(
		iqm_transcode, "build_iqm_circuit",
		lambda *args, **kwargs: object())
	resource = _QrmiResource(["Running"])
	driver = _qrmi_driver(resource)
	driver._resource = lambda: types.SimpleNamespace(
		Payload=types.SimpleNamespace(IQMServer=lambda **kwargs: kwargs))
	driver._target = lambda credential=None: {
		"dynamic_quantum_architecture": {"qubits": ["QB1"]}}
	driver._build_iqmjson = lambda circuit, shots, calset: ("{}", {})
	# A zero timeout makes a regression fail at once rather than poll the
	# stub job for the default 300 seconds.
	circuit = types.SimpleNamespace(
		info={"qasm": "OPENQASM 2.0;", "timeout": 0.0, "poll_interval": 0.0},
		cancel_event=_cancelled())

	with pytest.raises(
			DEFwExecutionError, match="cancelled before it was submitted"):
		driver.run_circuit(circuit)

	assert resource.started == []


# --- QDMI: cancelling the FoMaC job -----------------------------------------

class _FomacJob:
	def __init__(self, statuses, cancel_error=None):
		self.statuses = list(statuses)
		self.cancel_error = cancel_error
		self.cancels = 0

	def check(self):
		if len(self.statuses) > 1:
			return self.statuses.pop(0)
		return self.statuses[0]

	def cancel(self):
		self.cancels += 1
		if self.cancel_error:
			raise RuntimeError(self.cancel_error)


def _qdmi_driver():
	return qdmi_driver.QdmiDriver({"provider": "iqm"})


def test_a_cancel_cancels_the_qdmi_job():
	job = _FomacJob(["RUNNING"])

	status = _qdmi_driver()._poll_job(
		job, 60, 5.0, cancel_event=_cancelled())

	assert status == "cancelled"
	assert job.cancels == 1


def test_a_timeout_cancels_the_qdmi_job():
	job = _FomacJob(["RUNNING"])

	with pytest.raises(DEFwExecutionError, match="and it was cancelled"):
		_qdmi_driver()._poll_job(job, 0.0, 0.0)

	assert job.cancels == 1


def test_a_failed_qdmi_cancel_is_reported_with_the_timeout():
	job = _FomacJob(["RUNNING"], cancel_error="device busy")

	with pytest.raises(
			DEFwExecutionError, match=r"job\.cancel\(\) failed: device busy"):
		_qdmi_driver()._poll_job(job, 0.0, 0.0)


def test_a_finished_qdmi_job_is_not_cancelled():
	job = _FomacJob(["DONE"])

	status = _qdmi_driver()._poll_job(
		job, 60, 0.0, cancel_event=threading.Event())

	assert status == "completed"
	assert job.cancels == 0


def test_a_cancel_before_submission_submits_no_qdmi_job(monkeypatch):
	import util.iqm_transcode as iqm_transcode
	monkeypatch.setattr(
		iqm_transcode, "build_iqm_circuit",
		lambda *args, **kwargs: object())
	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "extract_topology",
		lambda device: {"qubits": ["QB1"]})
	submitted = []
	device = types.SimpleNamespace(
		submit_job=lambda *args: submitted.append(args))
	driver = _qdmi_driver()
	driver._device = lambda: device
	driver._ids = lambda: ("iqm", "device-a")
	driver._serialize_program = lambda iqm_circuit: "{}"
	circuit = types.SimpleNamespace(
		info={"qasm": "OPENQASM 2.0;", "timeout": 0.0, "poll_interval": 0.0},
		cancel_event=_cancelled())

	with pytest.raises(
			DEFwExecutionError, match="cancelled before it was submitted"):
		driver.run_circuit(circuit)

	assert submitted == []


# --- the shim QRC: the QPM's cancel hook ------------------------------------

class _Circuit:
	def __init__(self, cid="cid-1"):
		self.info = {"qasm": "OPENQASM 2.0;"}
		self._cid = cid
		self.states = []
		self.launch_time = self.creation_time = self.exec_time = 0
		self.completion_time = self.resources_consumed_time = 0

	def get_cid(self):
		return self._cid

	def set_launching(self):
		self.states.append("launching")

	def set_running(self):
		self.states.append("running")

	def set_exec_done(self):
		self.states.append("done")

	def set_fail(self):
		self.states.append("failed")

	def free_resources(self, circuit, result=None):
		self.states.append("freed")


class _Frontend:
	# Stands in for a driver. It waits on the circuit's cancel_event the way
	# _poll_task waits on the provider, and finishes on its own otherwise.
	def __init__(self, wait):
		self.wait = wait
		self.saw_cancel = threading.Event()

	def run_circuit(self, circuit, lib=None):
		if not circuit.cancel_event.wait(self.wait):
			return {"counts": {"00": 10}}
		self.saw_cancel.set()
		raise DEFwExecutionError(
			"QRMI job job-1 finished with status 'cancelled'")

	def capability_map(self):
		return {}


class _Event:
	# Stands in for api_events.Event, which the mock conftest stubs without
	# arguments. The QPM controller reads a pushed result through these two.
	def __init__(self, evtype, ev_info):
		self._evtype = evtype
		self._ev_info = ev_info

	def get_evtype(self):
		return self._evtype

	def get_event(self):
		return self._ev_info


def _shim_qrc(monkeypatch, wait=5.0):
	frontend = _Frontend(wait)
	monkeypatch.setattr(
		svc_qrc, "resolve_descriptor", lambda: {"libraries": []})
	monkeypatch.setattr(
		svc_qrc, "Frontend", lambda drivers, descriptor: frontend)
	monkeypatch.setattr(svc_qrc, "Event", _Event)
	return svc_qrc.QRC(start=False)


def test_a_cancel_for_an_unknown_circuit_is_not_found(monkeypatch):
	assert _shim_qrc(monkeypatch).cancel("no-such-cid") == "not-found"


def test_the_shim_cancels_a_running_circuit(monkeypatch):
	qrc = _shim_qrc(monkeypatch)
	circuit = _Circuit()

	assert qrc.async_run(circuit) == "cid-1"
	assert qrc.cancel("cid-1") == "cancelled"
	qrc.threads[-1].join(timeout=5)

	assert qrc.frontend.saw_cancel.is_set()
	result = qrc.read_cq("cid-1")
	assert result["rc"] == -1
	assert result["reason"] == "provider-cancelled"
	assert "failed" in circuit.states
	# The circuit is forgotten once its runner ends.
	assert qrc.cancel("cid-1") == "not-found"


def test_a_circuit_that_finishes_leaves_nothing_to_cancel(monkeypatch):
	qrc = _shim_qrc(monkeypatch, wait=0.0)

	qrc.async_run(_Circuit())
	qrc.threads[-1].join(timeout=5)

	result = qrc.read_cq("cid-1")
	assert result["rc"] == 0
	assert "reason" not in result
	assert qrc.cancel("cid-1") == "not-found"


def test_shutdown_stops_circuits_in_flight(monkeypatch):
	qrc = _shim_qrc(monkeypatch)
	qrc.async_run(_Circuit())

	qrc.shutdown()

	assert qrc.frontend.saw_cancel.is_set()


# --- end to end: a cancel through the QPM controller ------------------------

def test_the_qpm_cancels_a_running_shim_circuit(monkeypatch):
	# Before the shim had a cancel hook, the controller had no provider
	# canceller for it. It answered CANCEL_PENDING with
	# provider_cancel_status "unsupported", and the provider job ran on.
	from tests.mock.fakes import FakeSchedulerContext
	from tests.mock.test_qpm_controller import (
		FakeAdmissionContext, _setup_qpm)
	from util.qpm.controller import QPM_TASK_CANCELLED
	from util.qpm.util_qpm import UTIL_QPM

	_setup_qpm(monkeypatch)
	qrc = _shim_qrc(monkeypatch)
	qpm = UTIL_QPM(
		qrc,
		target_id="shim-cancel",
		admission_context_factory=FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext)

	# The client path: the QPM schedules the circuit, hands it to the shim,
	# and binds the cid the shim returns as the provider handle.
	submitted = qpm.async_run(
		{"qasm": "OPENQASM 2.0;", "num_qubits": 2}, reservation_id="1")
	cid = submitted["cid"]
	assert qrc.threads, "the QPM did not hand the circuit to the shim"

	cancelled = qpm.cancel_task(cid=cid, reservation_id="1", reason="test")

	assert cancelled["lifecycle_state"] == QPM_TASK_CANCELLED
	assert cancelled["provider_cancel_status"] == "cancelled"
	qrc.threads[-1].join(timeout=5)
	assert qrc.frontend.saw_cancel.is_set()
