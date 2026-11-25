# Guards the shim against a provider that rate-limits it with HTTP 429. On
# 2026-10-23 a chemistry workflow on the ORNL q20 failed on its first energy
# evaluation with "QRMI task_start failed: [QrmiError_] error in response:
# status code 429 Too Many Requests". With one circuit in flight, the next run
# failed the same way on task_status. Three parts:
#
# - The QRMI driver waits a 429 out, doubling the wait each time with jitter,
#   for each call of a job's lifecycle: task_start, task_status and
#   task_result. It gives up only once the job's own timeout has run out.
#   QRMI passes on no Retry-After, so the wait is the driver's.
# - Fewer status polls: unless the client names a poll_interval, the first
#   wait is the device's poll-interval-seconds, else a second, and it doubles
#   while the job runs, up to a minute. Both drivers poll this way.
# - A device-access max-concurrent-jobs caps how many of the device's circuits
#   are at the provider at once. It is the device profile's
#   max_provider_queue_depth, so the QPM controller holds the rest queued and
#   dispatches each as another finishes. The controller's operator limit,
#   max_inflight, is 1 unless an operator raises it, and the smaller of the
#   two wins.
#
# The provider side is stubbed, so no QRMI is needed. A fake clock stands in
# for the driver's waits, so nothing here sleeps them for real.

import collections
import json
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

import util.qpm.util_circuit as util_circuit  # noqa: E402
import util.qpm.util_qpm as util_qpm  # noqa: E402
from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm import svc_qrc  # noqa: E402
from svc_lib_qpm.drivers import qdmi_driver, qrmi_driver  # noqa: E402
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402
from tests.mock.fakes import FakeSchedulerContext  # noqa: E402
from util.qpm.controller import (  # noqa: E402
	QPM_TASK_QUEUED,
	_clear_target_controllers_for_tests,
)


# How QRMI 0.26 words a 429 from each vendor (see RATE_LIMITED). The
# response bodies IBM and Pasqal append are stand-ins.
IQM_429 = "error in response: status code 429 Too Many Requests"
IBM_429 = 'response body: 429 Too Many Requests {"errors": []}'
PASQAL_429 = 'Status: 429 Too Many Requests, Fail {"message": ""}'

ALWAYS = float("inf")
RESULT = {"measurement_counts": {"00": 10}}


class QrmiError_(RuntimeError):
	# The class QRMI raises for a 429, named as qrmi names it.
	pass


class _Clock:
	# Stands in for the driver's time module. A sleep moves the clock on
	# rather than waiting.
	def __init__(self):
		self.now = 1000.0
		self.sleeps = []

	def monotonic(self):
		return self.now

	def sleep(self, seconds):
		self.sleeps.append(seconds)
		self.now += seconds


def _fake_waits(monkeypatch):
	# The fake clock, and jitter that always picks the top of its range, so
	# each wait is the doubling ceiling itself.
	clock = _Clock()
	monkeypatch.setattr(qrmi_driver, "time", clock)
	monkeypatch.setattr(qrmi_driver, "random", types.SimpleNamespace(
		uniform=lambda low, high: high))
	return clock


class _Resource:
	# A QRMI resource whose calls are refused as rate-limited a set number of
	# times before they go through. Its job reports states in turn, and the
	# last one from then on.
	def __init__(self, start=0, status=0, result=0, message=IQM_429,
			states=("Completed",)):
		self.refusals = {
			"task_start": start, "task_status": status, "task_result": result}
		self.message = message
		self.states = list(states)
		self.reads = 0
		self.calls = collections.Counter()
		self.stopped = []

	def _call(self, name):
		self.calls[name] += 1
		if self.calls[name] <= self.refusals[name]:
			raise QrmiError_(self.message)

	def task_start(self, payload):
		self._call("task_start")
		return "job-1"

	def task_status(self, job_id):
		self._call("task_status")
		self.reads += 1
		return self.states[min(self.reads, len(self.states)) - 1]

	def task_result(self, job_id):
		self._call("task_result")
		return types.SimpleNamespace(value=json.dumps(RESULT))

	def task_stop(self, job_id):
		self.stopped.append(job_id)


def _driver(resource, **descriptor):
	driver = QrmiDriver(dict({"provider": "iqm", "id": "q20"}, **descriptor))
	driver._qpu = lambda credential=None: resource
	return driver


def _circuit(cancel_event=None, **info):
	return types.SimpleNamespace(
		info=dict({"num_shots": 10, "poll_interval": 0.0}, **info),
		get_cid=lambda: "cid-1", cancel_event=cancel_event)


# --- recognizing a 429 ---------------------------------------------------------

@pytest.mark.parametrize("message", [IQM_429, IBM_429, PASQAL_429])
def test_each_vendors_429_reads_as_a_rate_limit(message):
	assert qrmi_driver._rate_limited(QrmiError_(message))


@pytest.mark.parametrize("message", [
	"error in response: status code 500 Internal Server Error",
	"error in response: status code 403 Forbidden",
	"authentication failed: the token was rejected",
	"task not found: job 429",
])
def test_other_failures_do_not(message):
	assert not qrmi_driver._rate_limited(QrmiError_(message))


def test_the_wait_doubles_to_its_cap_and_is_jittered_below_it():
	for retry, ceiling in enumerate((2, 4, 8, 16, 32, 60, 60)):
		for _ in range(20):
			assert ceiling / 2 <= qrmi_driver._rate_limit_wait(retry) <= ceiling
	# A job with no timeout can retry for as long as it takes, and its wait
	# stays at the cap rather than overflowing.
	assert qrmi_driver._rate_limit_wait(100_000) <= 60


# --- submission ------------------------------------------------------------------

@pytest.mark.parametrize("message", [IQM_429, IBM_429, PASQAL_429])
def test_a_submission_refused_three_times_goes_through_on_the_fourth(
		monkeypatch, message):
	clock = _fake_waits(monkeypatch)
	resource = _Resource(start=3, message=message)

	result, job = _driver(resource)._run_sampler_payload(
		"payload", _circuit())

	assert resource.calls["task_start"] == 4
	assert clock.sleeps == [2.0, 4.0, 8.0]
	assert result == RESULT
	assert job["id"] == "job-1"
	assert job["status"] == "completed"


def test_the_submission_gives_up_when_the_job_timeout_runs_out(monkeypatch):
	clock = _fake_waits(monkeypatch)
	start = clock.now
	resource = _Resource(start=ALWAYS)

	with pytest.raises(DEFwExecutionError) as excinfo:
		_driver(resource, job_timeout_seconds=25)._run_sampler_payload(
			"payload", _circuit())

	# 2 + 4 + 8 seconds, then the 16-second wait is cut to the 11 left, one
	# more try is made at the deadline, and nothing is tried after it.
	assert clock.sleeps == [2.0, 4.0, 8.0, 11.0]
	assert clock.now - start == 25.0
	assert resource.calls["task_start"] == 5
	assert resource.calls["task_status"] == 0
	assert str(excinfo.value) == (
		"QRMI task_start was still rate-limited when the 25.0s job timeout "
		"ran out, after 4 retries: [QrmiError_] " + IQM_429)


def test_a_client_timeout_bounds_the_submission_too(monkeypatch):
	clock = _fake_waits(monkeypatch)
	resource = _Resource(start=ALWAYS)

	with pytest.raises(DEFwExecutionError, match=r"the 6\.0s job timeout"):
		_driver(resource, job_timeout_seconds=900)._run_sampler_payload(
			"payload", _circuit(timeout=6))

	assert clock.sleeps == [2.0, 4.0]


def test_without_a_job_timeout_the_submission_waits_until_it_goes_through(
		monkeypatch):
	# A Pasqal device without job-timeout-seconds waits for as long as Pasqal
	# Cloud queues the job, so it waits out a rate limit for as long too.
	clock = _fake_waits(monkeypatch)
	resource = _Resource(start=8, message=PASQAL_429)

	_, job = _driver(resource, provider="pasqal")._run_sampler_payload(
		"payload", _circuit(), delete_finished=False, default_timeout=None)

	assert job["status"] == "completed"
	assert resource.calls["task_start"] == 9
	assert clock.sleeps == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]


def test_a_failure_that_is_not_a_rate_limit_is_not_retried(monkeypatch):
	clock = _fake_waits(monkeypatch)
	resource = _Resource(
		start=ALWAYS,
		message="error in response: status code 500 Internal Server Error")

	with pytest.raises(
			DEFwExecutionError,
			match=r"^QRMI task_start failed: \[QrmiError_\] error in "
			r"response: status code 500"):
		_driver(resource)._run_sampler_payload("payload", _circuit())

	assert resource.calls["task_start"] == 1
	assert clock.sleeps == []


def test_a_cancel_ends_a_submission_wait_with_nothing_submitted(monkeypatch):
	# Real time here: the wait is the cancel event's, and a cancel ends it.
	monkeypatch.setattr(qrmi_driver, "RATE_LIMIT_FIRST_WAIT", 30.0)
	resource = _Resource(start=ALWAYS)
	cancel = threading.Event()
	threading.Timer(0.05, cancel.set).start()
	started = time.monotonic()

	with pytest.raises(
			DEFwExecutionError,
			match="cancelled while its task_start waited out a rate limit"):
		_driver(resource)._run_sampler_payload(
			"payload", _circuit(cancel_event=cancel))

	assert time.monotonic() - started < 5.0
	assert resource.calls["task_start"] == 1
	assert resource.stopped == []


# --- status polls and the result -------------------------------------------------

def test_a_refused_status_poll_is_waited_out_and_made_again(monkeypatch):
	clock = _fake_waits(monkeypatch)
	resource = _Resource(status=2)

	_, job = _driver(resource)._run_sampler_payload(
		"payload", _circuit(poll_interval=3.0))

	assert job["status"] == "completed"
	assert resource.calls["task_status"] == 3
	# A wait is never shorter than the poll interval.
	assert clock.sleeps == [3.0, 4.0]


def test_polls_refused_until_the_timeout_stop_the_job(monkeypatch):
	clock = _fake_waits(monkeypatch)
	resource = _Resource(status=ALWAYS)

	with pytest.raises(
			DEFwExecutionError,
			match=r"^QRMI job job-1 timed out after 10\.0s \(its status "
			r"polls were rate-limited\), and it was stopped$"):
		_driver(resource, job_timeout_seconds=10)._run_sampler_payload(
			"payload", _circuit())

	assert clock.sleeps == [2.0, 4.0, 4.0]
	assert resource.stopped == ["job-1"]
	assert resource.calls["task_result"] == 0


def test_time_spent_waiting_to_submit_comes_off_the_job_timeout(monkeypatch):
	# One deadline for the whole job: 6 of its 20 seconds went to waiting
	# out the rate limit, so it is stopped after 14 seconds of polling.
	clock = _fake_waits(monkeypatch)
	start = clock.now
	resource = _Resource(start=2, states=("Running",))

	with pytest.raises(
			DEFwExecutionError,
			match=r"timed out after 20\.0s \(last status 'running'\)"):
		_driver(resource, job_timeout_seconds=20)._run_sampler_payload(
			"payload", _circuit(poll_interval=1.0))

	assert clock.sleeps[:2] == [2.0, 4.0]
	assert clock.now - start == 20.0
	assert resource.calls["task_status"] == 15
	assert resource.stopped == ["job-1"]


def test_a_refused_result_fetch_is_waited_out(monkeypatch):
	clock = _fake_waits(monkeypatch)
	resource = _Resource(result=1)

	result, job = _driver(resource)._run_sampler_payload(
		"payload", _circuit())

	assert result == RESULT
	assert job["status"] == "completed"
	assert resource.calls["task_result"] == 2
	assert clock.sleeps == [2.0]


# --- how often a job's status is polled ------------------------------------------
#
# A circuit without a poll_interval, which is every circuit QFwBackend sends,
# is polled after the device's poll-interval-seconds, else a second, and each
# poll that finds the job unfinished doubles the wait, up to a minute.

def test_status_polls_back_off_while_the_job_runs(monkeypatch):
	clock = _fake_waits(monkeypatch)
	resource = _Resource(states=("Running",) * 8 + ("Completed",))

	_, job = _driver(resource, job_timeout_seconds=600)._run_sampler_payload(
		"payload", _circuit(poll_interval=None))

	assert job["status"] == "completed"
	assert clock.sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


def test_the_devices_poll_interval_is_the_first_wait(monkeypatch):
	clock = _fake_waits(monkeypatch)
	resource = _Resource(states=("Running",) * 3 + ("Completed",))

	_driver(resource, poll_interval_seconds=5)._run_sampler_payload(
		"payload", _circuit(poll_interval=None))

	assert clock.sleeps == [5.0, 10.0, 20.0]


def test_a_poll_interval_from_the_client_stays_fixed(monkeypatch):
	# A caller that names its interval, a measurement script for instance,
	# is polled at that interval, as before, whatever the device sets.
	clock = _fake_waits(monkeypatch)
	resource = _Resource(states=("Running",) * 3 + ("Completed",))

	_driver(resource, poll_interval_seconds=5)._run_sampler_payload(
		"payload", _circuit(poll_interval=3.0))

	assert clock.sleeps == [3.0, 3.0, 3.0]


def test_the_last_poll_is_made_when_the_timeout_runs_out(monkeypatch):
	clock = _fake_waits(monkeypatch)
	start = clock.now
	resource = _Resource(states=("Running",))

	with pytest.raises(
			DEFwExecutionError,
			match=r"timed out after 20\.0s \(last status 'running'\)"):
		_driver(resource, job_timeout_seconds=20)._run_sampler_payload(
			"payload", _circuit(poll_interval=None))

	# 1 + 2 + 4 + 8 seconds, then the 16-second wait is cut to the 5 left.
	assert clock.sleeps == [1.0, 2.0, 4.0, 8.0, 5.0]
	assert clock.now - start == 20.0
	assert resource.calls["task_status"] == 6
	assert resource.stopped == ["job-1"]


def test_a_poll_interval_that_is_not_a_number_fails_before_submitting(
		monkeypatch):
	_fake_waits(monkeypatch)
	resource = _Resource()

	with pytest.raises(
			DEFwExecutionError,
			match="the poll interval must be a number of seconds, not '1s'"):
		_driver(resource, poll_interval_seconds="1s")._run_sampler_payload(
			"payload", _circuit(poll_interval=None))

	assert resource.calls["task_start"] == 0


class _QdmiJob:
	def __init__(self, states):
		self.states = list(states)

	def check(self):
		return self.states.pop(0) if len(self.states) > 1 else self.states[0]


def test_qdmi_status_polls_back_off_too(monkeypatch):
	clock = _Clock()
	monkeypatch.setattr(qdmi_driver, "time", clock)
	job = _QdmiJob(["RUNNING"] * 3 + ["DONE"])

	status = qdmi_driver.QdmiDriver({"provider": "iqm"})._poll_job(
		job, 600, 1.0, backoff=True)

	assert status == "completed"
	assert clock.sleeps == [1.0, 2.0, 4.0]


# --- max-concurrent-jobs: the QPM's dispatch limit ---------------------------

class _Admission:
	# Admits everything, so the dispatch limit is all that holds work back.
	available = True

	def __init__(self, threading_mode):
		self.threading = threading_mode
		self.profiles = []

	def register_device_profile(self, profile):
		self.profiles.append(dict(profile))

	def set_policy(self, device_id, policy_name, options=None):
		pass

	def set_estimator(self, device_id, estimator_name, options=None):
		pass

	def get_reservation_record(self, reservation_id):
		return {
			"reservation_id": reservation_id,
			"state": "active",
			"expires_at_ns": 0,
		}

	def authorize_usage_request(self, reservation_id, usage):
		return {"status": "accepted", "reservation_id": reservation_id}

	def consume_usage_request(self, reservation_id, usage):
		return {"status": "accepted", "reservation_id": reservation_id}

	def return_usage_request(self, reservation_id, usage):
		pass

	def record_actual_request(self, reservation_id, actual):
		pass


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


class _Frontend:
	# Stands in for the drivers. A circuit is at the provider until the test
	# lets one finish, and the most that were there at once is kept.
	def __init__(self):
		self.lock = threading.Lock()
		self.running = 0
		self.most = 0
		self.started = 0
		self.finish = threading.Semaphore(0)

	def run_circuit(self, circuit, lib=None):
		with self.lock:
			self.started += 1
			self.running += 1
			self.most = max(self.most, self.running)
		try:
			if not self.finish.acquire(timeout=10):
				raise DEFwExecutionError("the test never let it finish")
		finally:
			with self.lock:
				self.running -= 1
		return {"counts": {"00": 10}}

	def capability_map(self):
		return {}


def _until(predicate, timeout=5.0):
	deadline = time.monotonic() + timeout
	while not predicate():
		if time.monotonic() > deadline:
			raise AssertionError("timed out waiting")
		time.sleep(0.01)


@pytest.fixture
def shim_qpm(monkeypatch):
	# Builds the shim QPM for a device whose descriptor carries the given
	# keys. Three host slots, so that what bounds the circuits is
	# max-concurrent-jobs and nothing else.
	_clear_target_controllers_for_tests()
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "h1,h2,h3")
	monkeypatch.delenv("QFW_QPU_DEVICE_ID", raising=False)
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	# The shim sets the qubits-per-process cap process wide.
	monkeypatch.setattr(
		util_circuit, "MAX_QUBITS_PP", util_circuit.MAX_QUBITS_PP)
	from svc_lib_qpm import descriptor as shim_descriptor
	from svc_lib_qpm.svc_qpm import QPM

	def build(frontend=None, **keys):
		descriptor = dict(
			{"id": "q20", "provider": "iqm", "libraries": []}, **keys)
		monkeypatch.setattr(
			shim_descriptor, "resolve_descriptor",
			lambda device_id=None: dict(descriptor))
		monkeypatch.setattr(
			svc_qrc, "resolve_descriptor", lambda: dict(descriptor))
		monkeypatch.setattr(
			svc_qrc, "Frontend",
			lambda drivers, descriptor: frontend or _Frontend())
		monkeypatch.setattr(svc_qrc, "Event", _Event)
		return QPM(
			start=False, admission_context_factory=_Admission,
			scheduler_context_factory=FakeSchedulerContext)

	yield build
	_clear_target_controllers_for_tests()


@pytest.mark.parametrize("keys,expected", [
	({"max_concurrent_jobs": 2}, 2),
	({"max_concurrent_jobs": "3"}, 3),
	({}, 0),
])
def test_the_device_limit_is_max_concurrent_jobs(shim_qpm, keys, expected):
	qpm = shim_qpm(**keys)

	limits = qpm.get_scheduler_status()["dispatch_limits"]
	assert limits["max_provider_queue_depth"] == expected
	# The operator's own limit is 1 until an operator raises it, and the
	# smaller nonzero one is what dispatch keeps to.
	assert limits["max_inflight"] == 1
	assert limits["effective_max_inflight"] == 1
	qpm.configure_dispatch_limits(limits={"max_inflight": 0})
	limits = qpm.get_scheduler_status()["dispatch_limits"]
	assert limits["effective_max_inflight"] == expected


@pytest.mark.parametrize("value", [0, -1, "two", 2.5, True])
def test_a_max_concurrent_jobs_that_is_not_a_count_stops_the_service(
		monkeypatch, value):
	from svc_lib_qpm import descriptor as shim_descriptor
	from svc_lib_qpm.svc_qpm import QPM
	monkeypatch.setattr(
		shim_descriptor, "resolve_descriptor", lambda device_id=None: {
			"id": "q20", "max_concurrent_jobs": value})

	with pytest.raises(
			DEFwExecutionError,
			match="max-concurrent-jobs for device 'q20' must be a whole "
			"number of jobs, at least 1"):
		QPM.__new__(QPM)._max_concurrent_jobs()


@pytest.mark.parametrize("max_inflight,limit,most", [
	# The QPM's default: one job at a time, with or without the key.
	(None, None, 1),
	(None, 2, 1),
	# An operator lifts that, and the device's own limit holds.
	(0, None, 3),
	(0, 1, 1),
	(0, 2, 2),
	(3, 2, 2),
])
def test_circuits_over_the_limit_wait_queued_and_run_as_others_finish(
		shim_qpm, max_inflight, limit, most):
	frontend = _Frontend()
	keys = {} if limit is None else {"max_concurrent_jobs": limit}
	qpm = shim_qpm(frontend=frontend, **keys)
	if max_inflight is not None:
		qpm.configure_dispatch_limits(limits={"max_inflight": max_inflight})
	info = {"qasm": "OPENQASM 2.0;", "num_qubits": 2, "reservation_id": "1"}

	responses = [qpm.async_run(dict(info)) for _ in range(3)]

	# The circuits over the limit wait in the QPM's queue rather than at
	# the provider.
	_until(lambda: frontend.started == most)
	queued = [response for response in responses
		if response["lifecycle_state"] == QPM_TASK_QUEUED]
	assert len(queued) == 3 - most
	# Each circuit that finishes lets the next queued one go.
	for _ in range(3):
		frontend.finish.release()
	for response in responses:
		_until(lambda: qpm.read_cq(
			cid=response["cid"], reservation_id="1").get("completion_ready"))
	assert frontend.started == 3
	assert frontend.most == most
