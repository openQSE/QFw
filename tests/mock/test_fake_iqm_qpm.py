import json
import time

import util.qpm.util_qpm as util_qpm
from tests.mock.fakes import FakeSchedulerContext
from svc_fake_iqm_qpm.svc_qpm import (
	FAKE_IQM_TARGET_ID,
	QPM,
)
from util.qpm.controller import _clear_target_controllers_for_tests


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
	_clear_target_controllers_for_tests()
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")
	monkeypatch.setenv("QFW_FAKE_QPM_MIN_SLEEP_SECONDS", "0.001")
	monkeypatch.setenv("QFW_FAKE_QPM_MAX_SLEEP_SECONDS", "0.01")
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)


def configure_fake_credentials(monkeypatch, tmp_path, *users):
	credential_db = {
		"users": {
			user: {
				"enabled": True,
				"devices": {
					FAKE_IQM_TARGET_ID: {
						"enabled": True,
						"api_key": f"fake-api-key-{user}",
					}
				}
			}
			for user in users
		}
	}
	(tmp_path / "qpu_users.json").write_text(
		json.dumps(credential_db), encoding="utf-8")
	config_path = tmp_path / "config.yaml"
	config_path.write_text(
		"\n".join([
			"qpus:",
			f"  {FAKE_IQM_TARGET_ID}:",
			"    provider: fake-iqm",
			"    provider-device-id: default",
			"    url: https://fake-iqm.invalid/",
			"    credential-db: qpu_users.json",
			"",
		]),
		encoding="utf-8")
	monkeypatch.setenv("QFW_DEVICE_ACCESS_CFG", str(config_path))


def test_fake_iqm_qpm_registers_profile_and_executes(monkeypatch, tmp_path):
	_setup(monkeypatch)
	configure_fake_credentials(monkeypatch, tmp_path, "stress-user")

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

	decision = qpm.reserve(request={
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
	assert completion["creation_time"] > 0
	assert completion["creation_time"] <= completion["launch_time"]
	assert completion["launch_time"] <= completion["exec_time"]
	assert completion["resources_consumed_time"] <= completion["exec_time"]
	assert completion["exec_time"] <= completion["completion_time"]
	assert completion["completion_time"] <= completion["cq_enqueue_time"]
	assert completion["cq_dequeue_time"] > completion["cq_enqueue_time"]
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
	timing = qpm.get_task_timing(
		reservation_id=decision["reservation_id"],
		task_id=response["qtask_id"])["timing"]
	assert timing["creation_time"] == completion["creation_time"]
	assert timing["launch_time"] == completion["launch_time"]
	assert timing["exec_time"] == completion["exec_time"]
	assert timing["completion_time"] == completion["completion_time"]
	assert timing["cq_enqueue_time"] == completion["cq_enqueue_time"]
	assert timing["cq_dequeue_time"] == -1


def test_fake_iqm_qpm_uses_reservation_provider_credential(
		monkeypatch, tmp_path):
	_setup(monkeypatch)
	configure_fake_credentials(monkeypatch, tmp_path, "stress-user")
	monkeypatch.setenv("QFW_QPM_CREDENTIAL_MODE", "required")

	qpm = QPM(
		admission_context_factory=FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext,
	)
	decision = qpm.reserve(request={
		"owner": {"user": "stress-user"},
		"job_id": "job-fake-iqm-credential",
		"scope_id": "allocation-credential",
		"target_device_id": FAKE_IQM_TARGET_ID,
		"task_class": {
			"qubit_count": 2,
			"depth": 4,
			"one_q_gate_count": 4,
			"two_q_gate_count": 1,
			"measurement_count": 2,
			"shots": 16,
		},
	})

	assert decision["status"] == "accepted"
	reservation = qpm.get_reservation(
		reservation_id=decision["reservation_id"])
	credential_binding = (
		reservation["request_metadata"]["provider_credential_binding"])
	assert credential_binding["provider_type"] == "file"
	assert credential_binding["user"] == "stress-user"
	assert credential_binding["target_device_id"] == FAKE_IQM_TARGET_ID
	assert credential_binding["secret_material"] == "cached-in-qpm"
	assert "fake-api-key-stress" not in str(reservation)

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"shots": 16,
		"depth": 4,
		"one_q_gate_count": 4,
		"two_q_gate_count": 1,
		"measurement_count": 2,
	}, reservation_id=decision["reservation_id"])
	completion = _wait_for_completion(
		qpm, response["cid"], decision["reservation_id"])

	assert completion["outcome"] == "COMPLETED"
	assert completion["provider_credential"] == {
		"api_key_present": True,
		"api_key_suffix": "user",
		"provider": "file",
		"provider_type": "file",
		"user": "stress-user",
		"target_device_id": FAKE_IQM_TARGET_ID,
		"provider_device_id": "default",
		"secret_material": "cached-in-qpm",
	}
	assert "fake-api-key-stress" not in str(completion)
	release = qpm.release(reservation_id=decision["reservation_id"])
	assert release["status"] == "accepted"
	assert decision["reservation_id"] not in (
		qpm.controller.reservation_credentials_by_id)


def _wait_for_completion(qpm, cid, reservation_id, timeout=1.0):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		completion = qpm.read_cq(cid=cid, reservation_id=reservation_id)
		if completion.get("completion_ready"):
			return completion
		time.sleep(0.01)
	raise AssertionError(f"completion timed out: cid={cid}")
