import threading
import time
import sys
import types

import pytest
import util.qpm.util_qpm as util_qpm
from defw_exception import DEFwExecutionError
from fakes import FakeSchedulerContext
from util.qpm.controller import (
	QPM_TASK_CANCELLED,
	QPM_TASK_FAILED,
	clear_target_controllers,
)
from util.qpm.util_qpm import UTIL_QPM
import util.qpm.admission as qpm_admission


class FakeQRC:
	def __init__(self):
		self.async_cids = []
		self.cancelled = []
		self.push_info = None

	def async_run(self, circuit):
		self.async_cids.append(circuit.get_cid())
		return circuit.get_cid()

	def sync_run(self, circuit):
		return {"cid": circuit.get_cid()}

	def cancel(self, provider_handle):
		self.cancelled.append(provider_handle)
		return "cancelled"

	def register_event_notification(self, info):
		self.push_info = info

	def shutdown(self):
		pass


class FakeAdmissionContext:
	available = True
	next_reservation_id = 100
	decision_status = "accepted"
	usage_status = "accepted"
	reservation_state = "active"
	expires_at_ns = 0
	estimate_response = None

	def __init__(self, threading_mode):
		self.threading = threading_mode
		self.lock = threading.Lock()
		self.requests = []
		self.registered_profiles = []
		self.policies = []
		self.estimators = []
		self.reservations = {}
		self.authorized = []
		self.consumed = []
		self.returned = []
		self.actual = []
		self.released = []
		self.cancelled = []
		self.expired = []
		self.calls = []
		self.estimates = []

	def evaluate_request(self, request):
		self.requests.append(("evaluate", dict(request)))
		return self._decision(request, reservation_id=0)

	def register_device_profile(self, profile):
		self.registered_profiles.append(dict(profile))

	def set_policy(self, device_id, policy_name, options=None):
		self.policies.append((device_id, policy_name, dict(options or {})))

	def set_estimator(self, device_id, estimator_name, options=None):
		self.estimators.append(
			(device_id, estimator_name, dict(options or {})))

	def reserve_request(self, request):
		with self.lock:
			reservation_id = FakeAdmissionContext.next_reservation_id
			FakeAdmissionContext.next_reservation_id += 1
		self.requests.append(("reserve", dict(request)))
		self.reservations[reservation_id] = {
			"reservation_id": reservation_id,
			"state": FakeAdmissionContext.reservation_state,
			"device_id": request["device_id"],
			"scope_id": request["scope_id"],
			"job_id": request["job_id"],
			"expires_at_ns": FakeAdmissionContext.expires_at_ns,
		}
		return self._decision(request, reservation_id=reservation_id)

	def estimate_qtask_class_request(self, device_id, task_class):
		self.estimates.append((device_id, dict(task_class)))
		if FakeAdmissionContext.estimate_response is None:
			return None
		return dict(FakeAdmissionContext.estimate_response)

	def renew_reservation(self, reservation_id, request):
		return {"status": "accepted", "reservation_id": reservation_id}

	def release_reservation(self, reservation_id, reason_code):
		self.released.append((reservation_id, reason_code))
		self.calls.append(("release", reservation_id, reason_code))
		self.reservations[reservation_id]["state"] = "released"
		return {"status": "accepted", "reservation_id": reservation_id}

	def cancel_reservation(self, reservation_id, reason_code):
		self.cancelled.append((reservation_id, reason_code))
		self.calls.append(("cancel", reservation_id, reason_code))
		self.reservations[reservation_id]["state"] = "cancelled"
		return {"status": "accepted", "reservation_id": reservation_id}

	def expire_reservations(self, now_ns):
		self.expired.append(now_ns)
		self.calls.append(("expire", now_ns))
		for reservation in self.reservations.values():
			expires_at_ns = reservation.get("expires_at_ns")
			if expires_at_ns and expires_at_ns <= now_ns:
				reservation["state"] = "expired"
		return 1

	def get_reservation_record(self, reservation_id):
		return dict(self.reservations[reservation_id])

	def list_reservation_records(self, filters):
		return [dict(item) for item in self.reservations.values()]

	def authorize_usage_request(self, reservation_id, usage):
		self.authorized.append((reservation_id, dict(usage)))
		return {
			"status": FakeAdmissionContext.usage_status,
			"reservation_id": reservation_id,
		}

	def consume_usage_request(self, reservation_id, usage):
		self.consumed.append((reservation_id, dict(usage)))
		return {
			"status": FakeAdmissionContext.usage_status,
			"reservation_id": reservation_id,
		}

	def return_usage_request(self, reservation_id, usage):
		self.returned.append((reservation_id, dict(usage)))
		self.calls.append(("return", reservation_id, usage.get("task_id")))

	def record_actual_request(self, reservation_id, actual):
		self.actual.append((reservation_id, dict(actual)))
		self.calls.append(("actual", reservation_id, actual.get("task_id")))

	def _decision(self, request, reservation_id):
		return {
			"status": FakeAdmissionContext.decision_status,
			"request_id": request["request_id"],
			"reservation_id": reservation_id,
			"reason": (
				"accepted" if FakeAdmissionContext.decision_status == "accepted"
				else "policy"),
		}


class AdmissionQPM(UTIL_QPM):
	def __init__(self, target_id="admission-target"):
		self.fake_qrc = FakeQRC()
		super().__init__(
			self.fake_qrc,
			target_id=target_id,
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=FakeSchedulerContext,
		)

	def prepare_circuit(self, info):
		info["qfw_backend"] = "hook"
		return info


class LegacyOptionAdmissionContext(FakeAdmissionContext):
	def __init__(self, threading_mode):
		super().__init__(threading_mode)
		self.loaded_configs = []

	def set_policy(self, device_id, policy_name):
		self.policies.append((device_id, policy_name))

	def set_estimator(self, device_id, estimator_name="baseline"):
		self.estimators.append((device_id, estimator_name))

	def load_config_string(self, yaml_text):
		self.loaded_configs.append(yaml_text)


class LegacyOptionAdmissionQPM(UTIL_QPM):
	def __init__(self, target_id="admission-legacy-options"):
		self.fake_qrc = FakeQRC()
		super().__init__(
			self.fake_qrc,
			target_id=target_id,
			admission_context_factory=LegacyOptionAdmissionContext,
			scheduler_context_factory=FakeSchedulerContext,
		)

	def prepare_circuit(self, info):
		info["qfw_backend"] = "hook"
		return info


class NonCancellableQRC:
	def __init__(self):
		self.async_cids = []
		self.push_info = None

	def async_run(self, circuit):
		self.async_cids.append(circuit.get_cid())
		return circuit.get_cid()

	def sync_run(self, circuit):
		return {"cid": circuit.get_cid()}

	def register_event_notification(self, info):
		self.push_info = info

	def shutdown(self):
		pass


class NonCancellableAdmissionQPM(UTIL_QPM):
	def __init__(self, target_id="admission-noncancellable"):
		self.fake_qrc = NonCancellableQRC()
		super().__init__(
			self.fake_qrc,
			target_id=target_id,
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=FakeSchedulerContext,
		)

	def prepare_circuit(self, info):
		info["qfw_backend"] = "hook"
		return info


def _setup(monkeypatch):
	clear_target_controllers()
	FakeAdmissionContext.next_reservation_id = 100
	FakeAdmissionContext.decision_status = "accepted"
	FakeAdmissionContext.usage_status = "accepted"
	FakeAdmissionContext.reservation_state = "active"
	FakeAdmissionContext.expires_at_ns = 0
	FakeAdmissionContext.estimate_response = None
	for env_name in (
			"QFW_SERVICE_RUNTIME_CONFIG",
			"QFW_QPM_COMPLETION_TTL_SECONDS",
			"QFW_QPM_COMPLETION_TERMINAL_RETENTION_SECONDS",
			"QFW_QPM_COMPLETION_TERMINAL_RESERVATION_RETENTION_SECONDS",
			"QFW_QPM_COMPLETION_MAX_RECORDS",
			"QFW_QPM_COMPLETION_MAX_RECORDS_PER_RESERVATION",
			"QFW_QPM_COMPLETION_MAX_BYTES",
			"QFW_QPM_COMPLETION_MAX_BYTES_PER_RESERVATION",
			"QFW_QPM_COMPLETION_PURGE_INTERVAL_SECONDS"):
		monkeypatch.delenv(env_name, raising=False)
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:2")
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)


def _assert_terminal_queue_garbage_collected(qpm, reservation_id, cid):
	terminal_at_ns = (
		qpm.controller.completion_queues[reservation_id].terminal_at_ns)
	terminal_retention_ns = int(
		qpm.controller.completion_retention[
			"terminal_reservation_retention_seconds"] *
		1_000_000_000)
	summary = qpm.controller.purge_completion_queues(
		now_ns=terminal_at_ns + terminal_retention_ns)
	result = qpm.peek_cq(cid=cid, reservation_id=reservation_id)

	assert reservation_id in summary["purged_reservations"]
	assert reservation_id not in qpm.controller.completion_queues
	assert result["outcome"] == "NO_LONGER_RETAINED"


def test_reserve_stores_unverified_request_metadata(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	decision = qpm.reserve({
		"owner": {"user": "alice"},
		"job_id": "job-7",
		"scope_id": "scope-a",
		"target_device_id": "device-a",
		"num_qubits": 4,
	})
	reservation = qpm.get_reservation(decision["reservation_id"])

	assert decision["status"] == "accepted"
	assert reservation["request_metadata"]["external_user_id"] == "alice"
	assert reservation["request_metadata"]["external_job_id"] == "job-7"
	assert qpm.controller.admission_context.requests[-1][1]["task_class"][
		"qubit_count"] == 4


def test_reserve_stores_structured_binding_without_provider_secrets(
		monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	decision = qpm.reserve({
		"owner": {"user": "alice"},
		"job_id": "slurm-77",
		"allocation_id": "alloc-77",
		"scope_id": "iqm-site",
		"target_device_id": "ornl-iqm-20q",
		"workload_kind": "hybrid",
		"walltime_ns": 10_000_000_000,
		"ttl_ns": 20_000_000_000,
		"task_class": {
			"count": 3,
			"qubit_count": 6,
			"depth": 24,
			"one_q_gate_count": 12,
			"two_q_gate_count": 5,
			"shots": 128,
			"measurement_count": 6,
		},
		"credential_hint": {
			"credential_db": "qpu_users.json",
			"user_record": "alice",
			"api_key": "secret-value",
		},
		"credential_handle": "iqm-handle-77",
		"analytics": {
			"application": "chemistry_example_aim2",
			"access_token": "secret-token",
		},
	})
	reservation = qpm.get_reservation(decision["reservation_id"])
	metadata = reservation["request_metadata"]
	binding = metadata["reservation_binding"]
	credential_binding = binding["provider_credential_binding"]

	assert binding["schema"] == "qfw-reservation-binding-v1"
	assert binding["launcher"]["external_user_id"] == "alice"
	assert binding["launcher"]["external_job_id"] == "slurm-77"
	assert binding["launcher"]["allocation_id"] == "alloc-77"
	assert binding["resource"]["target_device_id"] == "ornl-iqm-20q"
	assert binding["resource"]["workload_kind"] == "hybrid"
	assert binding["resource"]["task_class"] == {
		"class_id": 1,
		"count": 3,
		"qubit_count": 6,
		"depth": 24,
		"one_q_gate_count": 12,
		"two_q_gate_count": 5,
		"shots": 128,
		"measurement_count": 6,
	}
	assert "execution" in binding["allowed_operations"]
	assert credential_binding["credential_handle"] == "iqm-handle-77"
	assert credential_binding["credential_hint"]["credential_db"] == (
		"qpu_users.json")
	assert credential_binding["credential_hint"]["api_key"] == "<redacted>"
	assert credential_binding["secret_material"] == "not-stored"
	assert binding["analytics"]["application"] == "chemistry_example_aim2"
	assert binding["analytics"]["access_token"] == "<redacted>"
	assert metadata["provider_credential_binding"] == credential_binding


def test_completion_retention_loads_service_runtime_config(
		monkeypatch, tmp_path):
	_setup(monkeypatch)
	config = tmp_path / "service-runtime.yaml"
	config.write_text(
		"qpm:\n"
		"  completion-queues:\n"
		"    retention:\n"
		"      completion-ttl-seconds: 9\n"
		"      terminal-reservation-retention-seconds: 8\n"
		"      max-records-per-reservation: 7\n"
		"      max-bytes-per-reservation: 6\n"
		"      purge-interval-seconds: 5\n",
		encoding="utf-8")
	monkeypatch.setenv("QFW_SERVICE_RUNTIME_CONFIG", str(config))

	qpm = AdmissionQPM(target_id="admission-retention-config")
	retention = qpm.controller.completion_retention

	assert retention["completion_ttl_seconds"] == 9
	assert retention["terminal_reservation_retention_seconds"] == 8
	assert retention["max_records_per_reservation"] == 7
	assert retention["max_bytes_per_reservation"] == 6
	assert retention["purge_interval_seconds"] == 5


def test_completion_retention_accepts_documented_environment_overrides(
		monkeypatch):
	_setup(monkeypatch)
	monkeypatch.setenv("QFW_QPM_COMPLETION_TTL_SECONDS", "19")
	monkeypatch.setenv(
		"QFW_QPM_COMPLETION_TERMINAL_RETENTION_SECONDS", "18")
	monkeypatch.setenv("QFW_QPM_COMPLETION_MAX_RECORDS", "17")
	monkeypatch.setenv("QFW_QPM_COMPLETION_MAX_BYTES", "16")
	monkeypatch.setenv("QFW_QPM_COMPLETION_PURGE_INTERVAL_SECONDS", "15")

	qpm = AdmissionQPM(target_id="admission-retention-env")
	retention = qpm.controller.completion_retention

	assert retention["completion_ttl_seconds"] == 19
	assert retention["terminal_reservation_retention_seconds"] == 18
	assert retention["max_records_per_reservation"] == 17
	assert retention["max_bytes_per_reservation"] == 16
	assert retention["purge_interval_seconds"] == 15


def test_completion_retention_rejects_invalid_environment_overrides(
		monkeypatch):
	for env_name, value in (
			("QFW_QPM_COMPLETION_TTL_SECONDS", "0"),
			("QFW_QPM_COMPLETION_TERMINAL_RETENTION_SECONDS", "-1"),
			("QFW_QPM_COMPLETION_MAX_RECORDS", "many")):
		_setup(monkeypatch)
		monkeypatch.setenv(env_name, value)
		with pytest.raises(ValueError, match="completion retention"):
			AdmissionQPM(target_id=f"admission-retention-invalid-{env_name}")


def test_completion_retention_rejects_invalid_service_runtime_config(
		monkeypatch, tmp_path):
	_setup(monkeypatch)
	config = tmp_path / "service-runtime.yaml"
	config.write_text(
		"qpm:\n"
		"  completion-queues:\n"
		"    retention:\n"
		"      completion-ttl-seconds: 5\n"
		"      terminal-reservation-retention-seconds: 4\n"
		"      max-records-per-reservation: 3\n"
		"      max-bytes-per-reservation: 2\n"
		"      purge-interval-seconds: never\n",
		encoding="utf-8")
	monkeypatch.setenv("QFW_SERVICE_RUNTIME_CONFIG", str(config))

	with pytest.raises(ValueError, match="purge-interval-seconds"):
		AdmissionQPM(target_id="admission-retention-invalid-config")


def test_legacy_service_reserve_release_are_rejected(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	for call in (
			lambda: qpm.reserve("service-info"),
			lambda: qpm.release(["service-info"]),
			lambda: qpm.release(),
	):
		try:
			call()
		except DEFwExecutionError as exc:
			assert "legacy service" in str(exc)
		else:
			raise AssertionError("expected legacy QPM compatibility call")


def test_admission_decision_kinds_are_structured(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	for status in ("accepted", "delayed", "rejected"):
		FakeAdmissionContext.decision_status = status
		decision = qpm.evaluate({"num_qubits": 2})
		assert decision["status"] == status
		assert "reason" in decision


def test_policy_configuration_reaches_admission_context(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	qpm.configure_device_profile(device_id=77, profile={"max_qubits": 8})
	result = qpm.configure_admission_policy(
		token="opaque-token",
		device_id=77,
		policy_name="credit",
		policy_options={"total_credits": 16},
		estimator_name="baseline",
		estimator_options={"minimum_ns": 25},
	)

	assert result["status"] == "accepted"
	assert qpm.controller.admission_context.policies == [
		(77, "unlimited", {}),
		(77, "credit", {"total_credits": 16})]
	assert qpm.controller.admission_context.estimators == [
		(77, "baseline", {"minimum_ns": 25})]
	assert result["admission_policy"]["policy_name"] == "credit"
	assert result["estimator_policy"]["estimator_policy"][
		"estimator_name"] == "baseline"


def test_repeated_device_profile_configuration_is_idempotent(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	first = qpm.configure_device_profile(
		device_id=77, profile={"max_qubits": 8})
	second = qpm.configure_device_profile(
		device_id=77, profile={"max_qubits": 8})

	assert first["status"] == "accepted"
	assert second["status"] == "unchanged"
	assert second["version"] == first["version"]
	assert len(qpm.controller.admission_context.registered_profiles) == 1
	assert qpm.controller.admission_context.policies == [
		(77, "unlimited", {})]


def test_repeated_admission_policy_configuration_is_idempotent(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	qpm.configure_device_profile(device_id=77, profile={"max_qubits": 8})
	first = qpm.set_admission_policy(
		{"name": "credit", "options": {"total_credits": 16}},
		device_id=77)
	second = qpm.set_admission_policy(
		{"name": "credit", "options": {"total_credits": 16}},
		device_id=77)

	assert first["status"] == "accepted"
	assert second["status"] == "unchanged"
	assert second["version"] == first["version"]
	assert qpm.controller.admission_context.policies == [
		(77, "unlimited", {}),
		(77, "credit", {"total_credits": 16}),
	]


def test_policy_options_reach_native_fallback_config(monkeypatch):
	_setup(monkeypatch)
	qpm = LegacyOptionAdmissionQPM()

	qpm.configure_device_profile(device_id=77, profile={"max_qubits": 8})
	result = qpm.configure_admission_policy(
		token="opaque-token",
		device_id=77,
		policy_name="credit",
		policy_options={
			"allow_overcommit": True,
			"overcommit_credits": 2,
		},
		estimator_name="baseline",
		estimator_options={"observed_device_ns": 25},
	)
	configs = qpm.controller.admission_context.loaded_configs

	assert result["status"] == "accepted"
	assert "allow_overcommit: true" in configs[0]
	assert "overcommit_credits: 2" in configs[0]
	assert "observed_device_ns: 25" in configs[-1]
	assert "device_id: 77" in configs[-1]


def test_capacity_model_updates_admission_device_profile(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	qpm.configure_device_profile(device_id=77, profile={"max_qubits": 8})
	result = qpm.set_capacity_model(capacity_model={
		"credits": 64,
		"rate": 4,
		"concurrency": 2,
		"ttl_ns": 9_000,
		"window_ns": 60_000,
	})
	profile = qpm.controller.admission_context.registered_profiles[-1]

	assert result["status"] == "accepted"
	assert result["device_profile_version"] == 2
	assert profile["total_credits"] == 64
	assert profile["device_rate"] == 4
	assert profile["concurrent_jobs"] == 2
	assert profile["default_ttl_ns"] == 9_000
	assert profile["time_span_ns"] == 60_000


def test_capacity_model_uses_api_device_id_without_profile(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()

	result = qpm.set_capacity_model(
		token="opaque-token",
		device_id=77,
		capacity_model={
			"credits": 64,
			"rate": 4,
			"concurrency": 2,
		},
	)
	profile = qpm.controller.admission_context.registered_profiles[-1]

	assert result["status"] == "accepted"
	assert result["capacity_model"]["device_id"] == 77
	assert profile["device_id"] == 77
	assert profile["total_credits"] == 64
	assert profile["device_rate"] == 4
	assert profile["concurrent_jobs"] == 2
	assert profile["max_qubits"] == 1


def test_default_policy_paths_include_qhw_admission_source_build(
		monkeypatch, tmp_path):
	policy_dir = tmp_path / "qhw-admission" / "build" / "policies"
	package_dir = tmp_path / "qhw-admission" / "python" / "qhw_admission"
	policy_dir.mkdir(parents=True)
	package_dir.mkdir(parents=True)
	fake_module = types.ModuleType("qhw_admission")
	fake_module.__file__ = str(package_dir / "__init__.py")
	monkeypatch.setitem(sys.modules, "qhw_admission", fake_module)
	monkeypatch.delenv(qpm_admission.POLICY_PATH_ENV, raising=False)
	monkeypatch.delenv("QFW_PREFIX", raising=False)

	assert str(policy_dir) in qpm_admission._default_policy_paths()


def test_qtask_usage_uses_admission_estimator_output(monkeypatch):
	_setup(monkeypatch)
	FakeAdmissionContext.estimate_response = {
		"execution_ns": 12_000,
		"measurement_ns": 2_000,
		"compile_ns": 500,
		"transfer_ns": 300,
		"control_overhead_ns": 200,
		"total_ns": 15_000,
		"baseline_units": 7,
		"confidence_ppm": 1_000_000,
	}
	qpm = AdmissionQPM()
	decision = qpm.reserve({
		"owner": {"user": "alice"},
		"job_id": "job-7",
		"scope_id": "scope-a",
		"target_device_id": "admission-target",
		"num_qubits": 4,
	})

	status = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 4,
		"shots": 10,
		"depth": 12,
		"one_q_gate_count": 20,
		"two_q_gate_count": 5,
	}, reservation_id=decision["reservation_id"])

	usage = qpm.controller.admission_context.consumed[-1][1]
	scheduler_task = qpm.controller.scheduler_context.submitted[-1]
	circuit = qpm.circuits[status["cid"]]

	assert usage["estimated_ns"] == 15_000
	assert usage["baseline_units"] == 7
	assert usage["credits"] == 7
	assert usage["rate_units"] == 7
	assert scheduler_task["estimated_runtime_ns"] == 15_000
	assert scheduler_task["estimated_cost"] == 7
	assert circuit.info["admission_estimate"]["total_ns"] == 15_000
	assert circuit.info["admission_estimate"]["baseline_units"] == 7
	assert qpm.controller.admission_context.estimates[-1][1] == {
		"class_id": 1,
		"count": 1,
		"qubit_count": 4,
		"depth": 12,
		"one_q_gate_count": 20,
		"two_q_gate_count": 5,
		"shots": 10,
		"measurement_count": 1,
	}


def test_execution_rejects_invalid_reservation_state(monkeypatch):
	_setup(monkeypatch)
	FakeAdmissionContext.reservation_state = "cancelled"
	qpm = AdmissionQPM()
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]

	try:
		qpm.async_run({
			"qasm": "OPENQASM 2.0;",
			"num_qubits": 2,
			"reservation_id": reservation_id,
		})
	except DEFwExecutionError as exc:
		assert "state=cancelled" in str(exc)
	else:
		raise AssertionError("expected invalid reservation state")


def test_execution_rejects_reservation_binding_mismatches(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()
	reservation_id = qpm.reserve({
		"owner": {"user": "alice"},
		"job_id": "job-a",
		"scope_id": "scope-a",
		"target_device_id": "device-a",
		"session_id": "session-a",
		"run_context": {"operation": "async_run"},
		"num_qubits": 2,
	})["reservation_id"]
	base_info = {
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
		"job_id": "job-a",
		"scope_id": "scope-a",
		"target_device_id": "device-a",
		"session_id": "session-a",
		"run_context": {"operation": "async_run"},
	}

	for binding, updates in (
			("device_id", {"target_device_id": "device-b"}),
			("scope_id", {"scope_id": "scope-b"}),
			("job_id", {"job_id": "job-b"}),
			("session_id", {"session_id": "session-b"}),
			("operation", {"run_context": {"operation": "sync_run"}}),
	):
		info = dict(base_info)
		info.update(updates)
		try:
			qpm.async_run(info)
		except DEFwExecutionError as exc:
			assert f"reservation {binding} mismatch" in str(exc)
		else:
			raise AssertionError(
				f"expected reservation {binding} mismatch")

	assert qpm.fake_qrc.async_cids == []


def test_public_execution_accepts_positional_reservation_id(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="admission-positional-async")
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]

	async_response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
	}, reservation_id)

	assert async_response["outcome"] == "ACCEPTED"
	assert async_response["reservation_id"] == reservation_id
	assert qpm.fake_qrc.async_cids == [async_response["cid"]]

	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="admission-positional-sync")
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]

	sync_response = qpm.sync_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
	}, reservation_id)

	assert sync_response["outcome"] == "COMPLETED"
	assert sync_response["reservation_id"] == reservation_id
	assert qpm.controller.admission_context.consumed[-1][0] == reservation_id


def test_usage_authorization_hold_and_pending_retry(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"num_shots": 8,
		"reservation_id": reservation_id,
	})
	cid = response["cid"]
	runtime = qpm.controller.task_for_cid(cid)
	assert runtime.qtask_id in qpm.controller.capacity_holds
	assert qpm.controller.admission_context.consumed[-1][1]["task_id"] == (
		runtime.qtask_id)

	_setup(monkeypatch)
	FakeAdmissionContext.usage_status = "delayed"
	qpm = AdmissionQPM()
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	cid = response["cid"]
	runtime = qpm.controller.task_for_cid(cid)
	assert runtime.qtask_id in qpm.controller.pending_capacity
	FakeAdmissionContext.usage_status = "accepted"
	result = qpm.retry_pending_capacity(reservation_id)
	assert result[0]["status"] == "accepted"
	assert qpm.fake_qrc.async_cids == [cid]


def _delayed_capacity_qpm(monkeypatch, target_id="admission-delayed"):
	_setup(monkeypatch)
	FakeAdmissionContext.usage_status = "delayed"
	qpm = AdmissionQPM(target_id=target_id)
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	runtime = qpm.controller.task_for_cid(response["cid"])

	assert runtime.qtask_id in qpm.controller.pending_capacity
	assert qpm.fake_qrc.async_cids == []
	return qpm, reservation_id, runtime


def test_pending_capacity_retry_rejects_inactive_reservation(monkeypatch):
	for state in ("released", "cancelled"):
		qpm, reservation_id, runtime = _delayed_capacity_qpm(
			monkeypatch, target_id=f"admission-delayed-{state}")
		qpm.controller.admission_context.reservations[
			reservation_id]["state"] = state
		FakeAdmissionContext.usage_status = "accepted"

		result = qpm.retry_pending_capacity(reservation_id)

		assert result[0]["status"] == "rejected"
		assert result[0]["decision"]["reason"] == "invalid-reservation"
		assert f"state={state}" in result[0]["decision"]["message"]
		assert runtime.qtask_id not in qpm.controller.pending_capacity
		assert runtime.qtask_id not in qpm.controller.capacity_holds
		assert runtime.state == QPM_TASK_FAILED
		assert len(qpm.controller.admission_context.authorized) == 1
		assert qpm.controller.admission_context.authorized[0][0] == (
			reservation_id)
		assert qpm.controller.admission_context.consumed == []
		assert qpm.fake_qrc.async_cids == []


def test_pending_capacity_retry_rejects_expired_reservation(monkeypatch):
	qpm, reservation_id, runtime = _delayed_capacity_qpm(
		monkeypatch, target_id="admission-delayed-expired")
	qpm.controller.admission_context.reservations[
		reservation_id]["expires_at_ns"] = time.time_ns() - 1
	FakeAdmissionContext.usage_status = "accepted"

	result = qpm.retry_pending_capacity(reservation_id)
	close_state = qpm.controller.reservation_close_state[reservation_id]

	assert result[0]["status"] == "rejected"
	assert result[0]["decision"]["reason"] == "invalid-reservation"
	assert "expired reservation" in result[0]["decision"]["message"]
	assert runtime.qtask_id not in qpm.controller.pending_capacity
	assert runtime.qtask_id not in qpm.controller.capacity_holds
	assert runtime.state == QPM_TASK_CANCELLED
	assert runtime.qtask_id in close_state["pending_removed"]
	assert qpm.controller.admission_context.expired
	assert qpm.controller.admission_context.consumed == []
	assert qpm.fake_qrc.async_cids == []


def test_release_cancel_and_expiration_reconcile_active_state(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	cid = response["cid"]
	qtask_id = qpm.controller.task_for_cid(cid).qtask_id

	qpm.release(reservation_id, reason=16)
	close_state = qpm.controller.reservation_close_state[reservation_id]

	assert qpm.controller.admission_context.returned[-1][1]["task_id"] == qtask_id
	assert qpm.controller.admission_context.actual[-1][1]["task_id"] == qtask_id
	assert qpm.controller.admission_context.released == [(reservation_id, 16)]
	assert qpm.fake_qrc.cancelled == [response["provider_handle"]]
	assert close_state["scheduler_cancelled"] == [qtask_id]
	assert close_state["provider_cancelled"][0]["qtask_id"] == qtask_id

	_setup(monkeypatch)
	qpm = AdmissionQPM()
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	qpm.cancel(reservation_id, reason=4)
	assert qpm.controller.admission_context.cancelled == [(reservation_id, 4)]

	_setup(monkeypatch)
	FakeAdmissionContext.expires_at_ns = time.time_ns() - 1
	qpm = AdmissionQPM()
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	try:
		qpm.async_run({
			"qasm": "OPENQASM 2.0;",
			"num_qubits": 2,
			"reservation_id": reservation_id,
		})
	except DEFwExecutionError as exc:
		assert "expired reservation" in str(exc)
	else:
		raise AssertionError("expected expired reservation")
	assert qpm.controller.admission_context.expired


def test_reservation_close_cancelled_tasks_do_not_block_completion_queue_gc(
		monkeypatch):
	for close_kind, reason in (
			("release", 21),
			("cancel", 22),
			("expire", 23)):
		_setup(monkeypatch)
		monkeypatch.setenv(
			"QFW_QPM_COMPLETION_TERMINAL_RESERVATION_RETENTION_SECONDS",
			"1")
		qpm = AdmissionQPM(
			target_id=f"admission-close-retention-gc-{close_kind}")
		reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
		response = qpm.async_run({
			"qasm": "OPENQASM 2.0;",
			"num_qubits": 2,
			"reservation_id": reservation_id,
		})

		if close_kind == "release":
			result = qpm.release(reservation_id, reason=reason)
		elif close_kind == "cancel":
			result = qpm.cancel(reservation_id, reason=reason)
		else:
			now_ns = time.time_ns()
			qpm.controller.admission_context.reservations[
				reservation_id]["expires_at_ns"] = now_ns - 1
			result = qpm.controller.close_expired_reservation(
				reservation_id, now_ns=now_ns)

		runtime = qpm.controller.task_for_cid(response["cid"])

		assert result["status"] == "accepted"
		assert runtime.state == QPM_TASK_CANCELLED
		assert response["qtask_id"] in (
			qpm.controller.qtask_ids_by_reservation[reservation_id])
		_assert_terminal_queue_garbage_collected(
			qpm, reservation_id, response["cid"])


def test_expiration_sweep_reconciles_all_expired_holds_before_expire(
		monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM()
	reservation_a = qpm.reserve({"num_qubits": 2})["reservation_id"]
	reservation_b = qpm.reserve({"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_b,
	})
	qtask_id = response["qtask_id"]
	now_ns = time.time_ns()
	for reservation_id in (reservation_a, reservation_b):
		qpm.controller.admission_context.reservations[
			reservation_id]["expires_at_ns"] = now_ns - 1

	result = qpm.controller.close_expired_reservation(
		reservation_a, now_ns=now_ns)
	calls = qpm.controller.admission_context.calls
	expire_index = next(
		index for index, call in enumerate(calls)
		if call == ("expire", now_ns))
	return_index = calls.index(("return", reservation_b, qtask_id))
	actual_index = calls.index(("actual", reservation_b, qtask_id))
	close_state_a = qpm.controller.reservation_close_state[reservation_a]
	close_state_b = qpm.controller.reservation_close_state[reservation_b]

	assert result["status"] == "accepted"
	assert return_index < expire_index
	assert actual_index < expire_index
	assert qtask_id not in qpm.controller.capacity_holds
	assert close_state_a["status"] == "accepted"
	assert close_state_b["held_reconciled"] == [qtask_id]
	assert close_state_b["status"] == "accepted"
	assert qpm.controller.admission_context.reservations[
		reservation_a]["state"] == "expired"
	assert qpm.controller.admission_context.reservations[
		reservation_b]["state"] == "expired"


def test_release_waits_when_provider_cancellation_is_unsupported(monkeypatch):
	_setup(monkeypatch)
	qpm = NonCancellableAdmissionQPM()
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	qtask_id = response["qtask_id"]

	result = qpm.release(reservation_id, reason=16)
	close_state = qpm.controller.reservation_close_state[reservation_id]
	runtime = qpm.controller.task_for_cid(response["cid"])

	assert result["status"] == "pending"
	assert result["reason"] == "provider-cancel-pending"
	assert qtask_id in result["pending_qtask_ids"]
	assert qtask_id in close_state["provider_cancel_pending"]
	assert qtask_id in qpm.controller.capacity_holds
	assert runtime.state == "submitted"
	assert qpm.controller.admission_context.released == []
	assert qpm.controller.admission_context.returned == []
	assert qpm.controller.admission_context.actual == []

	circuit = qpm.circuits[response["cid"]]
	qpm.complete_provider_submission(
		circuit,
		result={"cid": response["cid"], "qtask_id": qtask_id},
	)
	finished = qpm.release(reservation_id, reason=16)

	assert finished["status"] == "accepted"
	assert close_state["provider_cancel_pending"] == []
	assert close_state["provider_cancel_resolved"] == [qtask_id]
	assert qtask_id not in qpm.controller.capacity_holds
	assert qpm.controller.admission_context.released == [(reservation_id, 16)]
	assert qpm.controller.admission_context.returned[-1][1]["task_id"] == qtask_id


def test_shared_context_handles_concurrent_reservations(monkeypatch):
	_setup(monkeypatch)
	qpm1 = AdmissionQPM(target_id="shared-target")
	qpm2 = AdmissionQPM(target_id="shared-target")
	results = []

	def reserve(qpm, index):
		results.append(qpm.reserve({
			"owner": {"user": f"user-{index}"},
			"job_id": f"job-{index}",
			"num_qubits": 2,
		})["reservation_id"])

	threads = [
		threading.Thread(target=reserve, args=(qpm1, 1)),
		threading.Thread(target=reserve, args=(qpm2, 2)),
	]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()

	assert qpm1.controller is qpm2.controller
	assert qpm1.controller.admission_context is qpm2.controller.admission_context
	assert sorted(results) == [100, 101]
