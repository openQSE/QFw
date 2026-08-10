import time

import util.qpm.util_qpm as util_qpm
from fakes import FakeSchedulerContext
from svc_fake_iqm_qpm.svc_qpm import (
	FAKE_IQM_TARGET_ID,
	QPM,
)
from util.qpm.controller import clear_target_controllers


class FakeAdmissionContext:
	available = True

	def __init__(self, threading_mode):
		self.threading = threading_mode
		self.registered_profiles = []
		self.policies = []
		self.estimators = []
		self.requests = []
		self.estimates = []
		self.authorized = []
		self.consumed = []
		self.returned = []
		self.actual = []
		self.reservations = {}
		self.next_reservation_id = 1

	def register_device_profile(self, profile):
		self.registered_profiles.append(dict(profile))

	def set_policy(self, device_id, policy_name, options=None):
		self.policies.append((device_id, policy_name, dict(options or {})))

	def set_estimator(self, device_id, estimator_name, options=None):
		self.estimators.append(
			(device_id, estimator_name, dict(options or {})))

	def reserve_request(self, request):
		reservation_id = self.next_reservation_id
		self.next_reservation_id += 1
		self.requests.append(dict(request))
		self.reservations[reservation_id] = {
			"reservation_id": reservation_id,
			"state": "active",
			"device_id": request["device_id"],
			"scope_id": request["scope_id"],
			"job_id": request["job_id"],
			"expires_at_ns": 0,
		}
		return {
			"status": "accepted",
			"request_id": request["request_id"],
			"reservation_id": reservation_id,
			"reason": "accepted",
		}

	def get_reservation_record(self, reservation_id):
		return dict(self.reservations[reservation_id])

	def estimate_qtask_class_request(self, device_id, task_class):
		self.estimates.append((device_id, dict(task_class)))
		return {
			"execution_ns": 40_000,
			"measurement_ns": 5_000,
			"compile_ns": 2_000,
			"transfer_ns": 1_000,
			"control_overhead_ns": 500,
			"total_ns": 48_500,
			"baseline_units": 4,
			"confidence_ppm": 1_000_000,
		}

	def authorize_usage_request(self, reservation_id, usage):
		self.authorized.append((reservation_id, dict(usage)))
		return {"status": "accepted", "reservation_id": reservation_id}

	def consume_usage_request(self, reservation_id, usage):
		self.consumed.append((reservation_id, dict(usage)))
		return {"status": "accepted", "reservation_id": reservation_id}

	def return_usage_request(self, reservation_id, usage):
		self.returned.append((reservation_id, dict(usage)))

	def record_actual_request(self, reservation_id, actual):
		self.actual.append((reservation_id, dict(actual)))

	def release_reservation(self, reservation_id, reason_code):
		self.reservations[reservation_id]["state"] = "released"
		return {"status": "accepted", "reservation_id": reservation_id}


def _setup(monkeypatch):
	clear_target_controllers()
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")
	monkeypatch.setenv("QFW_FAKE_QPM_MIN_SLEEP_SECONDS", "0.001")
	monkeypatch.setenv("QFW_FAKE_QPM_MAX_SLEEP_SECONDS", "0.01")
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)


def test_fake_iqm_qpm_registers_profile_and_executes(monkeypatch):
	_setup(monkeypatch)

	qpm = QPM(
		admission_context_factory=FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext,
	)
	admission = qpm.controller.admission_context
	profile = admission.registered_profiles[-1]

	assert profile["external_device_id"] == FAKE_IQM_TARGET_ID
	assert profile["max_qubits"] == 20
	assert profile["baseline"]["qubit_count"] == 4
	assert admission.policies[-1][1] == "unlimited"

	decision = qpm.reserve({
		"owner": {"user": "stress-user"},
		"job_id": "job-fake-iqm",
		"scope_id": "allocation-1",
		"target_device_id": FAKE_IQM_TARGET_ID,
		"walltime_ns": 1_000_000_000,
		"task_class": {
			"qubit_count": 4,
			"depth": 12,
			"one_q_gate_count": 20,
			"two_q_gate_count": 6,
			"measurement_count": 4,
			"shots": 64,
		},
	})

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 4,
		"shots": 64,
		"depth": 12,
		"one_q_gate_count": 20,
		"two_q_gate_count": 6,
		"measurement_count": 4,
	}, reservation_id=decision["reservation_id"])
	completion = _wait_for_completion(
		qpm, response["cid"], decision["reservation_id"])

	assert response["outcome"] == "ACCEPTED"
	assert completion["outcome"] == "COMPLETED"
	assert completion["baseline_units"] == 4
	assert completion["estimated_device_ns"] == 48_500
	assert completion["requested_timing_metadata"] == {
		"num_qubits": 4,
		"shots": 64,
		"depth": 12,
		"one_q_gate_count": 20,
		"two_q_gate_count": 6,
		"measurement_count": 4,
	}
	assert admission.estimates[-1][1]["qubit_count"] == 4
	assert admission.consumed[-1][1]["baseline_units"] == 4
	assert admission.consumed[-1][1]["credits"] == 4
	assert admission.actual[-1][1]["actual_baseline_units"] == 4
	assert qpm.controller.scheduler_context.completed == [response["qtask_id"]]
	assert qpm.get_scheduler_queue_state()["provider_inflight_qtask_ids"] == []


def _wait_for_completion(qpm, cid, reservation_id, timeout=1.0):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		completion = qpm.read_cq(cid=cid, reservation_id=reservation_id)
		if completion.get("completion_ready"):
			return completion
		time.sleep(0.01)
	raise AssertionError(f"completion timed out: cid={cid}")
