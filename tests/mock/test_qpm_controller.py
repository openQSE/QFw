import util.qpm.util_qpm as util_qpm
from defw_exception import DEFwExecutionError
from fakes import FakeSchedulerContext
from util.qpm.controller import (
	QPM_TASK_CANCELLED,
	clear_target_controllers,
)
from util.qpm.request import parse_execution_request
from util.qpm.util_qpm import DIAGNOSTIC_BYPASS_ENV, UTIL_QPM


class FakeQRC:
	def __init__(self):
		self.sync_circuits = []
		self.async_circuits = []
		self.shutdown_called = False

	def sync_run(self, circuit):
		self.sync_circuits.append(circuit)
		assert circuit.info["provider_ready"] is True
		assert circuit.info["qfw_backend"] == "hook-backend"
		assert circuit.info["lock_held_during_qrc"] is False
		return {
			"cid": circuit.get_cid(),
			"qtask_id": circuit.info["qtask_id"],
		}

	def async_run(self, circuit):
		self.async_circuits.append(circuit)
		assert circuit.info["provider_ready"] is True
		return circuit.get_cid()

	def shutdown(self):
		self.shutdown_called = True


class FakeAdmissionContext:
	available = True

	def __init__(self, threading_mode):
		self.threading = threading_mode

	def get_reservation_record(self, reservation_id):
		return {
			"reservation_id": reservation_id,
			"state": "active",
			"expires_at_ns": 0,
		}

	def authorize_usage_request(self, reservation_id, usage):
		return {
			"status": "accepted",
			"reservation_id": reservation_id,
			"qtask_id": usage["task_id"],
		}

	def consume_usage_request(self, reservation_id, usage):
		return {
			"status": "accepted",
			"reservation_id": reservation_id,
			"qtask_id": usage["task_id"],
		}

	def return_usage_request(self, reservation_id, usage):
		pass

	def record_actual_request(self, reservation_id, actual):
		pass


class TrackingLock:
	def __init__(self):
		self.depth = 0

	def __enter__(self):
		self.depth += 1
		return self

	def __exit__(self, exc_type, exc, traceback):
		self.depth -= 1

	@property
	def held(self):
		return self.depth > 0


class HookQPM(UTIL_QPM):
	def __init__(self, target_id="target-a"):
		self.hooks = []
		super().__init__(
			FakeQRC(),
			target_id=target_id,
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=FakeSchedulerContext,
		)

	def prepare_circuit(self, info):
		self.hooks.append("prepare_circuit")
		info["qfw_backend"] = "hook-backend"
		return info

	def prepare_provider_submission(self, circuit):
		self.hooks.append("prepare_provider_submission")
		lock_held = getattr(self.controller.lock, "held", False)
		circuit.info["provider_ready"] = True
		circuit.info["lock_held_during_hook"] = lock_held
		circuit.info["lock_held_during_qrc"] = lock_held
		return circuit


def _setup_qpm(monkeypatch):
	clear_target_controllers()
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:2")
	monkeypatch.delenv(DIAGNOSTIC_BYPASS_ENV, raising=False)
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)


def test_controller_state_is_target_scoped(monkeypatch):
	_setup_qpm(monkeypatch)

	qpm_a1 = HookQPM(target_id="target-a")
	qpm_a2 = HookQPM(target_id="target-a")
	qpm_b = HookQPM(target_id="target-b")

	assert qpm_a1.controller is qpm_a2.controller
	assert qpm_a1.controller is not qpm_b.controller
	assert qpm_a1.free_hosts is qpm_a2.free_hosts
	assert qpm_a1.controller_telemetry()["binding_count"] == 2
	assert qpm_b.controller_telemetry()["binding_count"] == 1


def test_runtime_maps_allocate_stable_qtask_ids(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()
	qpm.controller.reservation_metadata_by_id["reservation-1"] = {
		"owner": {"user": "alice"},
		"external_user_id": "alice",
		"external_job_id": "job-7",
	}

	cid1 = qpm.create_circuit({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
		"token": "opaque-token",
	})
	cid2 = qpm.create_circuit({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 3,
		"reservation_id": "reservation-1",
	})

	runtime1 = qpm.controller.task_for_cid(cid1)
	runtime2 = qpm.controller.task_for_cid(cid2)
	assert runtime1.qtask_id == 1
	assert runtime2.qtask_id == 2
	assert qpm.circuits[cid1].info["qtask_id"] == 1
	assert "token" not in qpm.circuits[cid1].info
	assert qpm.controller.task_for_qtask_id(1) is runtime1
	assert qpm.controller.qtask_ids_by_reservation["reservation-1"] == {1, 2}
	assert runtime1.token_metadata == {"present": True, "type": "str"}
	assert runtime1.external_ids["owner_id"] == "alice"
	assert runtime1.canonical_ids["owner_id"] == runtime2.canonical_ids["owner_id"]
	assert runtime1.canonical_ids["job_id"] == runtime2.canonical_ids["job_id"]


def test_request_scoped_identifiers_are_canonicalized(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()

	cid = qpm.create_circuit({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
		"job_id": "job-direct",
		"allocation_id": "allocation-direct",
		"project_id": "project-direct",
		"session_id": "session-direct",
	})
	runtime = qpm.controller.task_for_cid(cid)

	assert runtime.external_ids["job_id"] == "job-direct"
	assert runtime.external_ids["allocation_id"] == "allocation-direct"
	assert runtime.external_ids["project_id"] == "project-direct"
	assert runtime.external_ids["session_id"] == "session-direct"
	assert runtime.canonical_ids["job_id"] == (
		qpm.controller.external_id_maps["job_id"]["job-direct"])
	assert runtime.canonical_ids["allocation_id"] == (
		qpm.controller.external_id_maps[
			"allocation_id"]["allocation-direct"])
	assert runtime.request_metadata["execution_context"]["job_id"] == "job-direct"
	assert runtime.request_metadata["execution_context"][
		"allocation_id"] == "allocation-direct"
	assert runtime.request_metadata["execution_context"][
		"project_id"] == "project-direct"
	assert runtime.request_metadata["execution_context"][
		"session_id"] == "session-direct"


def test_sync_run_records_opaque_token_metadata(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()

	result = qpm.sync_run(
		{"qasm": "OPENQASM 2.0;", "num_qubits": 2},
		reservation_id="reservation-1",
		token="opaque-token")
	runtime = qpm.controller.terminal_tasks_by_cid[result["cid"]]

	assert runtime.token_metadata == {"present": True, "type": "str"}


def test_async_run_records_dict_token_metadata(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()

	result = qpm.async_run(
		{"qasm": "OPENQASM 2.0;", "num_qubits": 2},
		reservation_id="reservation-1",
		token={"opaque": "token"})
	runtime = qpm.controller.task_for_cid(result["cid"])

	assert runtime.token_metadata == {"present": True, "type": "dict"}


def test_provider_hooks_run_outside_controller_lock(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()
	qpm.controller.lock = TrackingLock()

	result = qpm.sync_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	assert result["qtask_id"] == 1
	assert qpm.hooks == ["prepare_circuit", "prepare_provider_submission"]
	assert qpm.qrc.sync_circuits[0].info["lock_held_during_hook"] is False
	assert qpm.controller.task_for_cid(result["cid"]) is None


def test_cancellation_lookup_and_cleanup(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()
	cid = qpm.create_circuit({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})
	runtime = qpm.controller.task_for_cid(cid)

	qpm.controller.set_provider_canceller(lambda provider_handle: "cancelled")
	qpm.controller.bind_scheduler_task(runtime.qtask_id, "sched-1")
	qpm.controller.bind_provider_handle(runtime.qtask_id, "provider-1")
	cancelled = qpm.cancel_provider_submission(cid, reason="test")

	assert cancelled["lifecycle_state"] == QPM_TASK_CANCELLED
	assert qpm.controller.task_for_scheduler_task_id("sched-1") is runtime
	assert qpm.controller.task_for_provider_handle("provider-1") is runtime
	assert qpm.controller.cleanup_circuit(cid) is runtime
	assert qpm.controller.task_for_cid(cid) is None
	assert qpm.controller.task_for_qtask_id(runtime.qtask_id) is None
	assert qpm.controller.task_for_scheduler_task_id("sched-1") is None


def test_reservation_validation_uses_reservation_id_binding(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()
	reservation_id = "reservation-1"
	qpm.controller.reservation_metadata_by_id[reservation_id] = {
		"external_allocation_id": "allocation-a",
		"external_project_id": "project-a",
		"external_session_id": "session-a",
		"policy": {"queue": "priority"},
		"operation": "execution",
	}

	request = parse_execution_request(
		{},
		reservation_id=reservation_id,
	).context
	qpm.controller.validate_reservation_for_context(request)


def test_diagnostic_bypass_requires_configuration(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()

	try:
		qpm.async_run({"qasm": "OPENQASM 2.0;", "num_qubits": 2})
	except DEFwExecutionError as exc:
		assert "reservation_id is required" in str(exc)
	else:
		raise AssertionError("expected missing reservation to fail")

	try:
		qpm.diagnostic_async_run({"qasm": "OPENQASM 2.0;", "num_qubits": 2})
	except DEFwExecutionError as exc:
		assert "diagnostic bypass execution is disabled" in str(exc)
	else:
		raise AssertionError("expected disabled diagnostic bypass to fail")

	monkeypatch.setenv(DIAGNOSTIC_BYPASS_ENV, "yes")
	try:
		qpm.diagnostic_async_run(
			{"qasm": "OPENQASM 2.0;", "num_qubits": 2},
			reason="maintenance",
		)
	except DEFwExecutionError as exc:
		assert "requires authenticated request context" in str(exc)
	else:
		raise AssertionError("expected missing diagnostic auth to fail")

	try:
		qpm.diagnostic_async_run(
			{"qasm": "OPENQASM 2.0;", "num_qubits": 2},
		)
	except DEFwExecutionError as exc:
		assert "requires authenticated request context" in str(exc)
	else:
		raise AssertionError("expected missing diagnostic auth to fail")

	assert qpm.controller.diagnostic_bypass_records == []
	assert qpm.controller_telemetry()["diagnostic_bypass_enabled"] is True


def test_normal_execution_strips_forged_diagnostic_bypass(monkeypatch):
	_setup_qpm(monkeypatch)
	qpm = HookQPM()

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
		"_qfw_diagnostic_bypass": True,
	})
	runtime = qpm.controller.task_for_cid(response["cid"])

	assert runtime.diagnostic_bypass is False
	assert "_qfw_diagnostic_bypass" not in qpm.circuits[response["cid"]].info
	assert runtime.scheduler_task_id is not None
	assert runtime.qtask_id in qpm.controller.capacity_holds
	assert qpm.controller.diagnostic_bypass_records == []


def test_diagnostic_bypass_success_uses_explicit_token(monkeypatch):
	_setup_qpm(monkeypatch)
	monkeypatch.setenv(DIAGNOSTIC_BYPASS_ENV, "yes")
	qpm = HookQPM()

	response = qpm.diagnostic_async_run(
		{"qasm": "OPENQASM 2.0;", "num_qubits": 2},
		token={"opaque": "token"},
		reason="maintenance")
	runtime = qpm.controller.task_for_cid(response["cid"])
	record = qpm.controller.diagnostic_bypass_records[-1]

	assert runtime.diagnostic_bypass is True
	assert runtime.reservation_id is None
	assert runtime.scheduler_task_id is None
	assert runtime.qtask_id not in qpm.controller.capacity_holds
	assert record["operation"] == "diagnostic_async_run"
	assert record["reason"] == "maintenance"
	assert record["token_metadata"] == {"present": True, "type": "dict"}
