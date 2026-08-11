import util.qpm.util_qpm as util_qpm
from fakes import FakeSchedulerContext
from util.qpm.scheduler import NativeSchedulerContext, set_scheduler_policy
from util.qpm.controller import (
	QPM_TASK_CANCELLED,
	QPM_TASK_FAILED,
	QPM_TASK_QUEUED,
	clear_target_controllers,
)
from util.qpm.util_qpm import UTIL_QPM


class FakeQRC:
	def __init__(self):
		self.async_cids = []
		self.sync_cids = []
		self.cancelled = []
		self.cancel_status = "cancelled"
		self.async_error = None
		self.sync_error = None

	def async_run(self, circuit):
		self.async_cids.append(circuit.get_cid())
		if self.async_error is not None:
			raise self.async_error
		return f"provider-{circuit.get_cid()}"

	def sync_run(self, circuit):
		self.sync_cids.append(circuit.get_cid())
		if self.sync_error is not None:
			raise self.sync_error
		circuit.set_running()
		circuit.set_exec_done()
		return {
			"cid": circuit.get_cid(),
			"qtask_id": circuit.info["qtask_id"],
		}

	def cancel(self, provider_handle):
		self.cancelled.append(provider_handle)
		return self.cancel_status

	def shutdown(self):
		pass


class FakeAdmissionContext:
	available = True
	usage_status = "accepted"

	def __init__(self, threading_mode):
		self.threading = threading_mode
		self.authorized = []
		self.consumed = []
		self.returned = []
		self.actual = []
		self.released = []
		self.reservation_states = {}

	def get_reservation_record(self, reservation_id):
		return {
			"reservation_id": reservation_id,
			"state": self.reservation_states.get(
				reservation_id, "active"),
			"expires_at_ns": 0,
		}

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

	def record_actual_request(self, reservation_id, actual):
		self.actual.append((reservation_id, dict(actual)))

	def release_reservation(self, reservation_id, reason_code):
		self.released.append((reservation_id, reason_code))
		self.reservation_states[reservation_id] = "released"
		return {"status": "accepted", "reservation_id": reservation_id}


class SchedulerQPM(UTIL_QPM):
	def __init__(self, target_id="scheduler-target"):
		self.fake_qrc = FakeQRC()
		super().__init__(
			self.fake_qrc,
			target_id=target_id,
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=FakeSchedulerContext,
		)

	def prepare_circuit(self, info):
		info["qfw_backend"] = "scheduler-hook"
		return info


class FailingSubmitSchedulerContext(FakeSchedulerContext):
	def submit_task(self, task):
		self.submitted.append(dict(task))
		raise RuntimeError("scheduler submit failed")


class FailingSchedulerQPM(UTIL_QPM):
	def __init__(self, target_id="scheduler-submit-failure"):
		self.fake_qrc = FakeQRC()
		super().__init__(
			self.fake_qrc,
			target_id=target_id,
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=FailingSubmitSchedulerContext,
		)

	def prepare_circuit(self, info):
		info["qfw_backend"] = "scheduler-hook"
		return info


class DuplicateLoadNativeScheduler:
	def __init__(self):
		self.loaded = []
		self.policies = []

	def load_standard_plugin(self, name):
		if name in self.loaded:
			raise RuntimeError(f"duplicate scheduler plugin load: {name}")
		self.loaded.append(name)

	def set_policy(self, name, options=None):
		self.policies.append((name, options))


def _setup(monkeypatch):
	clear_target_controllers()
	FakeAdmissionContext.usage_status = "accepted"
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:2")
	monkeypatch.delenv(
		"QFW_QPM_COMPLETION_TERMINAL_RESERVATION_RETENTION_SECONDS",
		raising=False)
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


def test_native_standard_scheduler_plugin_load_is_idempotent():
	scheduler = DuplicateLoadNativeScheduler()
	context = NativeSchedulerContext(
		qhw_scheduler=object(),
		qpu=object(),
		scheduler=scheduler,
		threading_mode="thread-safe",
	)

	set_scheduler_policy(context, {"policy": "fifo"})
	set_scheduler_policy(context, {"policy": "fifo"})

	assert scheduler.loaded == ["fifo"]
	assert scheduler.policies == [("fifo", []), ("fifo", [])]


def test_scheduler_metrics_keep_retained_history_out_of_depth(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	qpm.controller.scheduler_context.queue.extend([1, 2, 3, 4])

	capacity = qpm.get_capacity_snapshot()
	metrics = qpm.get_queue_metrics()

	assert capacity["scheduler_task_count"] == 4
	assert capacity["scheduler_queue_depth"] == 0
	assert metrics["scheduler_task_count"] == 4
	assert metrics["scheduler_depth"] == 0


def test_scheduler_control_state_is_target_scoped(monkeypatch):
	_setup(monkeypatch)

	qpm_a = SchedulerQPM(target_id="sched-a")
	qpm_b = SchedulerQPM(target_id="sched-b")
	qpm_a_again = SchedulerQPM(target_id="sched-a")

	qpm_a.configure_scheduler_policy(
		configuration={"policy": "fifo", "options": {7: 3}})
	qpm_a.pause_execution_target(reason="operator")

	assert qpm_a.controller is qpm_a_again.controller
	assert qpm_a.controller is not qpm_b.controller
	assert qpm_a.get_scheduler_status()["state"] == "paused"
	assert qpm_b.get_scheduler_status()["state"] == "active"
	assert qpm_a.controller.scheduler_context.policy == ("fifo", {7: 3})


def test_admitted_qtask_enters_scheduler_before_provider(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	qpm.controller.reservation_metadata_by_id["reservation-1"] = {
		"owner": {"user": "alice"},
		"external_user_id": "alice",
		"external_job_id": "job-1",
	}

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})
	runtime = qpm.controller.task_for_cid(response["cid"])
	scheduler = qpm.controller.scheduler_context

	assert response["outcome"] == "ACCEPTED"
	assert response["lifecycle_state"] == "submitted"
	assert scheduler.submitted[0]["task_id"] == response["qtask_id"]
	assert scheduler.submitted[0]["owner_id"] != 0
	assert scheduler.submitted[0]["job_id"] != 0
	assert scheduler.started == [response["qtask_id"]]
	assert qpm.fake_qrc.async_cids == [response["cid"]]
	assert runtime.scheduler_task_id == response["qtask_id"]


def test_delayed_capacity_stays_out_of_scheduler(monkeypatch):
	_setup(monkeypatch)
	FakeAdmissionContext.usage_status = "delayed"
	qpm = SchedulerQPM()

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	assert response["outcome"] == "DELAYED"
	assert qpm.controller.scheduler_context.submitted == []
	assert qpm.fake_qrc.async_cids == []
	assert response["pending_queue_position"] == 1


def test_dispatch_depth_keeps_later_qtasks_queued(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	qpm.configure_dispatch_limits(limits={"max_inflight": 1})

	first = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})
	second = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	assert qpm.fake_qrc.async_cids == [first["cid"]]
	assert second["lifecycle_state"] == QPM_TASK_QUEUED
	assert second["scheduler_task_id"] == second["qtask_id"]


def test_provider_queue_depth_bounds_dispatch_and_reports_limits(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM(target_id="provider-depth")
	qpm.controller.device_profile = {
		"device_id": 1,
		"max_provider_queue_depth": 2,
	}
	qpm.configure_dispatch_limits(limits={"max_inflight": 0})

	responses = [qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	}) for _ in range(3)]
	status = qpm.get_scheduler_status()

	assert qpm.fake_qrc.async_cids == [
		responses[0]["cid"], responses[1]["cid"]]
	assert responses[2]["lifecycle_state"] == QPM_TASK_QUEUED
	assert status["dispatch_limits"] == {
		"max_inflight": 0,
		"max_provider_queue_depth": 2,
		"effective_max_inflight": 2,
		"provider_inflight": 2,
	}


def test_effective_dispatch_limit_uses_smallest_nonzero_limit(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM(target_id="combined-depth")

	for operator, device, expected in (
		(0, 0, 0),
		(3, 0, 3),
		(0, 4, 4),
		(3, 4, 3),
		(5, 2, 2),
	):
		qpm.controller.device_profile = {
			"device_id": 1,
			"max_provider_queue_depth": device,
		}
		qpm.configure_dispatch_limits(limits={"max_inflight": operator})
		limits = qpm.get_scheduler_status()["dispatch_limits"]
		assert limits["effective_max_inflight"] == expected


def test_lowering_provider_limit_does_not_cancel_inflight_work(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM(target_id="dynamic-provider-depth")
	qpm.configure_dispatch_limits(limits={"max_inflight": 3})
	responses = [qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	}) for _ in range(3)]

	qpm.controller.device_profile = {
		"device_id": 1,
		"max_provider_queue_depth": 1,
	}
	status = qpm.get_scheduler_status()

	assert status["dispatch_limits"]["effective_max_inflight"] == 1
	assert status["provider_inflight_count"] == 2
	assert qpm.fake_qrc.cancelled == []
	assert len(responses) == 3


def test_async_run_reports_new_qtask_when_older_work_dispatches(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	qpm.pause_execution_target(reason="operator")
	first = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	qpm.resume_execution_target()
	second = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	assert second["cid"] != first["cid"]
	assert second["qtask_id"] != first["qtask_id"]
	assert second["lifecycle_state"] == QPM_TASK_QUEUED
	assert qpm.fake_qrc.async_cids == [first["cid"]]


def test_sync_timeout_returns_task_handles(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	qpm.pause_execution_target(reason="operator")

	response = qpm.sync_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	}, timeout=0)
	status = qpm.task_status(
		qtask_id=response["qtask_id"], reservation_id="reservation-1")

	assert response["outcome"] == "TIMEOUT"
	assert response["lifecycle_state"] == QPM_TASK_QUEUED
	assert response["scheduler_task_id"] == response["qtask_id"]
	assert response["reason"] == "sync-timeout"
	assert status["outcome"] == "ACCEPTED"
	assert status["lifecycle_state"] == QPM_TASK_QUEUED
	assert status["timeout"]["reason"] == "sync-timeout"
	assert qpm.oor_queue.qsize() == 1


def test_completion_accounting_precedes_terminal_response(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()

	response = qpm.sync_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"estimated_ns": 100,
		"actual_ns": 40,
		"baseline_units": 10,
		"actual_baseline_units": 4,
		"credits": 7,
		"actual_credits": 2,
		"rate_units": 5,
		"actual_rate_units": 1,
		"reservation_id": "reservation-1",
	})
	context = qpm.controller.admission_context
	scheduler = qpm.controller.scheduler_context

	assert response["outcome"] == "COMPLETED"
	assert context.returned[-1][1]["task_id"] == response["qtask_id"]
	assert context.returned[-1][1]["estimated_ns"] == 60
	assert context.returned[-1][1]["baseline_units"] == 6
	assert context.returned[-1][1]["credits"] == 5
	assert context.returned[-1][1]["rate_units"] == 4
	assert context.actual[-1][1]["task_id"] == response["qtask_id"]
	assert context.actual[-1][1]["observed_device_ns"] == 40
	assert context.actual[-1][1]["actual_baseline_units"] == 4
	assert context.actual[-1][1]["actual_credits"] == 2
	assert context.actual[-1][1]["actual_rate_units"] == 1
	assert scheduler.completed == [response["qtask_id"]]


def test_scheduler_submission_failure_reconciles_capacity_hold(monkeypatch):
	_setup(monkeypatch)
	qpm = FailingSchedulerQPM()

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	status = qpm.task_status(qtask_id=1, reservation_id="reservation-1")
	completion = qpm.read_cq(
		cid=response["cid"], reservation_id="reservation-1")
	context = qpm.controller.admission_context

	assert response["outcome"] == "FAILED"
	assert response["lifecycle_state"] == QPM_TASK_FAILED
	assert response["cid"] == status["cid"]
	assert response["qtask_id"] == 1
	assert response["reason"] == "scheduler-submission-failed"
	assert "result" not in response
	assert response["error"]["reason"] == "scheduler-submission-failed"
	assert response["error"]["error"] == "scheduler submit failed"
	assert response["error"]["error_type"] == "RuntimeError"
	assert status["outcome"] == "FAILED"
	assert status["lifecycle_state"] == QPM_TASK_FAILED
	assert status["reason"] == "scheduler-submission-failed"
	assert "result" not in status
	assert status["error"]["reason"] == "scheduler-submission-failed"
	assert status["error"]["error"] == "scheduler submit failed"
	assert status["error"]["error_type"] == "RuntimeError"
	assert completion["completion_ready"] is True
	assert completion["outcome"] == "FAILED"
	assert completion["reason"] == "scheduler-submission-failed"
	assert completion["error"]["error"] == "scheduler submit failed"
	assert qpm.controller.capacity_holds == {}
	assert context.returned[-1][1]["task_id"] == 1
	assert context.actual[-1][1]["task_id"] == 1
	assert qpm.controller.scheduler_context.submitted[0]["task_id"] == 1
	assert qpm.fake_qrc.async_cids == []


def test_scheduler_failed_task_does_not_block_completion_queue_gc(
		monkeypatch):
	_setup(monkeypatch)
	monkeypatch.setenv(
		"QFW_QPM_COMPLETION_TERMINAL_RESERVATION_RETENTION_SECONDS", "1")
	qpm = FailingSchedulerQPM(
		target_id="scheduler-failed-retention-gc")

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	qpm.release("reservation-1", reason=9)

	assert response["outcome"] == "FAILED"
	assert response["qtask_id"] in (
		qpm.controller.qtask_ids_by_reservation["reservation-1"])
	_assert_terminal_queue_garbage_collected(
		qpm, "reservation-1", response["cid"])


def test_async_provider_failure_preserves_failed_terminal_state(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM(target_id="scheduler-async-provider-failure")
	qpm.fake_qrc.async_error = RuntimeError("provider async failed")

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	status = qpm.task_status(qtask_id=1, reservation_id="reservation-1")
	completion = qpm.read_cq(
		cid=response["cid"], reservation_id="reservation-1")
	context = qpm.controller.admission_context
	scheduler = qpm.controller.scheduler_context

	assert response["outcome"] == "FAILED"
	assert response["lifecycle_state"] == QPM_TASK_FAILED
	assert response["cid"] == status["cid"]
	assert response["qtask_id"] == 1
	assert response["reason"] == "provider-submission-failed"
	assert "result" not in response
	assert response["error"]["reason"] == "provider-submission-failed"
	assert response["error"]["error"] == "provider async failed"
	assert response["error"]["error_type"] == "RuntimeError"
	assert status["outcome"] == "FAILED"
	assert status["lifecycle_state"] == QPM_TASK_FAILED
	assert status["reason"] == "provider-submission-failed"
	assert "result" not in status
	assert status["error"]["reason"] == "provider-submission-failed"
	assert status["error"]["error"] == "provider async failed"
	assert status["error"]["error_type"] == "RuntimeError"
	assert completion["completion_ready"] is True
	assert completion["outcome"] == "FAILED"
	assert completion["reason"] == "provider-submission-failed"
	assert completion["error"]["error"] == "provider async failed"
	assert scheduler.failed == [1]
	assert scheduler.completed == []
	assert context.returned[-1][1]["task_id"] == 1
	assert context.actual[-1][1]["task_id"] == 1
	assert qpm.controller.capacity_holds == {}


def test_provider_failed_task_does_not_block_completion_queue_gc(
		monkeypatch):
	_setup(monkeypatch)
	monkeypatch.setenv(
		"QFW_QPM_COMPLETION_TERMINAL_RESERVATION_RETENTION_SECONDS", "1")
	qpm = SchedulerQPM(target_id="scheduler-provider-failed-retention-gc")
	qpm.fake_qrc.async_error = RuntimeError("provider async failed")

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	qpm.release("reservation-1", reason=10)

	assert response["outcome"] == "FAILED"
	_assert_terminal_queue_garbage_collected(
		qpm, "reservation-1", response["cid"])


def test_sync_provider_failure_preserves_failed_terminal_state(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM(target_id="scheduler-sync-provider-failure")
	qpm.fake_qrc.sync_error = RuntimeError("provider sync failed")

	response = qpm.sync_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	status = qpm.task_status(qtask_id=1, reservation_id="reservation-1")
	context = qpm.controller.admission_context
	scheduler = qpm.controller.scheduler_context

	assert response["outcome"] == "FAILED"
	assert response["lifecycle_state"] == QPM_TASK_FAILED
	assert response["cid"] == status["cid"]
	assert response["qtask_id"] == 1
	assert response["reason"] == "provider-submission-failed"
	assert "result" not in response
	assert response["error"]["reason"] == "provider-submission-failed"
	assert response["error"]["error"] == "provider sync failed"
	assert response["error"]["error_type"] == "RuntimeError"
	assert status["outcome"] == "FAILED"
	assert status["lifecycle_state"] == QPM_TASK_FAILED
	assert status["reason"] == "provider-submission-failed"
	assert "result" not in status
	assert status["error"]["reason"] == "provider-submission-failed"
	assert status["error"]["error"] == "provider sync failed"
	assert status["error"]["error_type"] == "RuntimeError"
	assert scheduler.failed == [1]
	assert scheduler.completed == []
	assert context.returned[-1][1]["task_id"] == 1
	assert context.actual[-1][1]["task_id"] == 1
	assert qpm.controller.capacity_holds == {}


def test_cancel_task_reconciles_scheduler_admission_and_provider(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	cancelled = qpm.cancel_task(
		qtask_id=response["qtask_id"], reservation_id="reservation-1",
		reason="user-request")
	completion = qpm.read_cq(
		cid=response["cid"], reservation_id="reservation-1")

	assert cancelled["outcome"] == "CANCELLED"
	assert cancelled["lifecycle_state"] == QPM_TASK_CANCELLED
	assert cancelled["provider_cancel_status"] == "cancelled"
	assert completion["completion_ready"] is True
	assert completion["outcome"] == "CANCELLED"
	assert completion["reason"] == "user-request"
	assert completion["provider_cancel_status"] == "cancelled"
	assert qpm.fake_qrc.cancelled == [response["provider_handle"]]
	assert qpm.controller.scheduler_context.cancelled == [response["qtask_id"]]
	assert qpm.controller.admission_context.returned[-1][1]["task_id"] == (
		response["qtask_id"])


def test_cancelled_task_does_not_block_completion_queue_gc(monkeypatch):
	_setup(monkeypatch)
	monkeypatch.setenv(
		"QFW_QPM_COMPLETION_TERMINAL_RESERVATION_RETENTION_SECONDS", "1")
	qpm = SchedulerQPM(target_id="scheduler-cancelled-retention-gc")
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	cancelled = qpm.cancel_task(
		qtask_id=response["qtask_id"], reservation_id="reservation-1",
		reason="user-request")
	qpm.release("reservation-1", reason=11)

	assert cancelled["outcome"] == "CANCELLED"
	assert response["qtask_id"] in (
		qpm.controller.qtask_ids_by_reservation["reservation-1"])
	_assert_terminal_queue_garbage_collected(
		qpm, "reservation-1", response["cid"])


def test_cancel_task_keeps_provider_pending_nonterminal(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	qpm.fake_qrc.cancel_status = "pending"
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	pending = qpm.cancel_task(
		qtask_id=response["qtask_id"], reservation_id="reservation-1",
		reason="user-request")
	runtime = qpm.controller.task_for_cid(response["cid"])

	assert pending["outcome"] == "CANCEL_PENDING"
	assert pending["lifecycle_state"] == "submitted"
	assert pending["reason"] == "provider-cancel-pending"
	assert pending["provider_cancel_status"] == "pending"
	assert runtime.state == "submitted"
	assert response["qtask_id"] in qpm.controller.provider_inflight
	assert response["qtask_id"] in qpm.controller.capacity_holds
	assert qpm.controller.admission_context.returned == []
	assert qpm.controller.scheduler_context.cancelled == []


def test_cancel_task_keeps_unsupported_provider_work_nonterminal(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})
	qpm.controller.set_provider_canceller(None)

	pending = qpm.cancel_task(
		qtask_id=response["qtask_id"], reservation_id="reservation-1",
		reason="user-request")
	runtime = qpm.controller.task_for_cid(response["cid"])

	assert pending["outcome"] == "CANCEL_PENDING"
	assert pending["provider_cancel_status"] == "unsupported"
	assert runtime.state == "submitted"
	assert response["qtask_id"] in qpm.controller.capacity_holds
	assert qpm.controller.admission_context.returned == []
	assert qpm.controller.scheduler_context.cancelled == []


def test_task_status_exposes_queue_observations(monkeypatch):
	_setup(monkeypatch)
	FakeAdmissionContext.usage_status = "delayed"
	qpm = SchedulerQPM()
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	status = qpm.task_status(
		cid=response["cid"], reservation_id="reservation-1")
	queue_state = qpm.get_scheduler_queue_state()

	assert status["outcome"] == "DELAYED"
	assert status["pending_queue_position"] == 1
	assert status["wait_estimate"]["available"] is False
	assert queue_state["runtime_tasks"][0]["qtask_id"] == response["qtask_id"]


def test_lifecycle_telemetry_records_controls_and_reconciliation(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	qpm.configure_scheduler_policy(configuration={"policy": "priority"})
	qpm.pause_execution_target(reason="operator")
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	qpm.controller.admission_context.reservation_states[
		"reservation-1"] = "expired"
	summary = qpm.reconcile_runtime_state(reason="test-lifecycle")
	telemetry = qpm.get_service_lifecycle_telemetry()
	status = qpm.task_status(
		qtask_id=response["qtask_id"], reservation_id="reservation-1")

	events = [record["event"] for record in telemetry["lifecycle_events"]]
	assert "binding-attached" in events
	assert "scheduler-policy-change" in events
	assert "scheduler-paused" in events
	assert "reconciliation" in events
	assert summary["capacity_hold_faults"][0]["reservation_state"] == (
		"expired")
	assert telemetry["reconciliation_faults"][0]["qtask_id"] == (
		response["qtask_id"])
	assert status["outcome"] == "FAILED"
	assert qpm.controller.scheduler_context.failed == [response["qtask_id"]]


def test_reconciliation_keeps_provider_active_hold_until_cancel_terminal(
		monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	qpm.fake_qrc.cancel_status = "pending"
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": "reservation-1",
	})

	qpm.controller.admission_context.reservation_states[
		"reservation-1"] = "expired"
	summary = qpm.reconcile_runtime_state(reason="test-provider-cancel")
	runtime = qpm.controller.task_for_cid(response["cid"])

	assert summary["capacity_hold_faults"][0]["reason"] == (
		"inactive-reservation-hold-provider-cancel-pending")
	assert summary["capacity_hold_faults"][0]["provider_cancel_status"] == (
		"pending")
	assert qpm.fake_qrc.cancelled == [response["provider_handle"]]
	assert response["qtask_id"] in qpm.controller.capacity_holds
	assert response["qtask_id"] in qpm.controller.provider_inflight
	assert runtime.state == "submitted"
	assert qpm.controller.scheduler_context.failed == []


def test_lifecycle_telemetry_records_defw_directory_events(monkeypatch):
	_setup(monkeypatch)
	qpm = SchedulerQPM()
	service_record = {
		"service_id": "qpm-iqm",
		"service_name": "QPM",
		"service_type": "qfw.qpm",
		"runtime_id": "runtime-2",
		"peer_handle": "peer-2",
		"generation": 2,
		"state": "UP",
	}

	qpm.record_defw_directory_event("registration", service_record)
	qpm.record_defw_directory_event(
		"PEER_LOST",
		dict(service_record, state="TIMED_OUT"),
		peer_event={
			"event_type": "PEER_LOST",
			"peer_handle": "peer-2",
			"remote_runtime_id": "runtime-2",
			"reason": "heartbeat-timeout",
			"timestamp": 101.5,
		})
	qpm.record_defw_directory_event(
		"deregistration",
		dict(service_record, state="DEREGISTERED"))
	qpm.record_defw_directory_event(
		"retention-purge",
		dict(service_record, retention_deadline=102.5))

	telemetry = qpm.get_service_lifecycle_telemetry()
	events = [record["event"] for record in telemetry["lifecycle_events"]]
	peer_lost = next(
		record for record in telemetry["lifecycle_events"]
		if record["event"] == "peer-lost")
	audit_events = [record["event"] for record in telemetry["audit_records"]]

	for event in (
			"service-registration",
			"service-restart",
			"generation-change",
			"peer-lost",
			"service-timeout",
			"service-deregistration",
			"retention-purge"):
		assert event in events
		assert event in audit_events
	assert peer_lost["reason"] == "heartbeat-timeout"
	assert peer_lost["details"]["peer_remote_runtime_id"] == "runtime-2"
