import time

from defw_exception import DEFwExecutionError
from tests.mock.fakes import FakeSchedulerContext
from tests.mock.test_qpm_admission import (
	AdmissionQPM,
	FakeAdmissionContext,
	_setup,
)
import util.qpm.util_qpm as util_qpm
from util.qpm.util_qpm import UTIL_QPM
from util.qpm.request import parse_execution_request


def _set_retention_site(monkeypatch, tmp_path, **retention):
	path = tmp_path / "site.yaml"
	lines = ["qpm:", "  completion-queues:", "    retention:"]
	for key, value in retention.items():
		lines.append(f"      {key}: {value}")
	path.write_text("\n".join(lines) + "\n", encoding="utf-8")
	monkeypatch.setenv("QFW_SITE_CONFIG", str(path))


class CapturingEventAPI:
	def __init__(self, class_id=None, target=None):
		self.class_id = class_id
		self.target = target
		self.events = []

	def put(self, event):
		self.events.append(event.get_event())
		return True


class FailingEventAPI(CapturingEventAPI):
	def put(self, event):
		del event
		raise RuntimeError("event endpoint is no longer reachable")


class CompletionEvent:
	def __init__(self, evtype, payload):
		self.evtype = evtype
		self.payload = payload

	def get_evtype(self):
		return self.evtype

	def get_event(self):
		return self.payload


class CompletingQRC:
	def __init__(self):
		self.async_cids = []
		self.circuit_results = []
		self.push_info = None

	def async_run(self, circuit):
		cid = circuit.get_cid()
		result = {
			"cid": cid,
			"qtask_id": circuit.info["qtask_id"],
		}
		self.async_cids.append(cid)
		circuit.set_exec_done()
		circuit.free_resources(circuit, result=result)
		if self.push_info:
			event = CompletionEvent(self.push_info["evtype"], result)
			delivered = self.push_info["class"].put(event)
			if delivered is not False:
				return cid
		self.circuit_results.append(result)
		return cid

	def sync_run(self, circuit):
		return {
			"cid": circuit.get_cid(),
			"qtask_id": circuit.info["qtask_id"],
		}

	def read_cq(self, cid=None):
		for index, result in enumerate(self.circuit_results):
			if cid is None or result["cid"] == cid:
				return self.circuit_results.pop(index)
		return None

	def peak_cq(self, cid=None):
		for result in self.circuit_results:
			if cid is None or result["cid"] == cid:
				return result
		return None

	def register_event_notification(self, info):
		self.push_info = info

	def shutdown(self):
		pass


class CompletingQPM(UTIL_QPM):
	def __init__(self, target_id="ops-completing"):
		self.fake_qrc = CompletingQRC()
		super().__init__(
			self.fake_qrc,
			target_id=target_id,
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=FakeSchedulerContext,
		)

	def prepare_circuit(self, info):
		info["qfw_backend"] = "completion-hook"
		return info


class FailingCompletionQRC(CompletingQRC):
	def async_run(self, circuit):
		cid = circuit.get_cid()
		result = {
			"cid": cid,
			"qtask_id": circuit.info["qtask_id"],
			"result": {
				"provider_error": "async provider failed",
			},
			"rc": 7,
		}
		self.async_cids.append(cid)
		circuit.set_fail()
		circuit.free_resources(circuit, result=result)
		if self.push_info:
			event = CompletionEvent(self.push_info["evtype"], result)
			delivered = self.push_info["class"].put(event)
			if delivered is not False:
				return cid
		self.circuit_results.append(result)
		return cid


class FailingCompletionQPM(UTIL_QPM):
	def __init__(self, target_id="ops-failing-completion"):
		self.fake_qrc = FailingCompletionQRC()
		super().__init__(
			self.fake_qrc,
			target_id=target_id,
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=FakeSchedulerContext,
		)

	def prepare_circuit(self, info):
		info["qfw_backend"] = "failing-completion-hook"
		return info


class PendingCompletionQRC:
	def __init__(self):
		self.async_cids = []
		self.push_info = None

	def async_run(self, circuit):
		self.async_cids.append(circuit.get_cid())
		return circuit.get_cid()

	def sync_run(self, circuit):
		return {"cid": circuit.get_cid()}

	def read_cq(self, cid=None):
		return None

	def peak_cq(self, cid=None):
		return None

	def register_event_notification(self, info):
		self.push_info = info

	def shutdown(self):
		pass


class PendingCompletionQPM(UTIL_QPM):
	def __init__(self, target_id="ops-pending-completion"):
		self.fake_qrc = PendingCompletionQRC()
		super().__init__(
			self.fake_qrc,
			target_id=target_id,
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=FakeSchedulerContext,
		)

	def prepare_circuit(self, info):
		info["qfw_backend"] = "pending-completion-hook"
		return info


def test_managed_operations_flow_reports_telemetry(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-target")
	reservation_id = qpm.reserve(request={
		"owner": {"user": "ops-user"},
		"job_id": "ops-job",
		"num_qubits": 2,
	})["reservation_id"]

	access_model = qpm.get_telemetry_access_model()
	capacity_before = qpm.get_capacity_snapshot()
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	status = qpm.task_status(
		qtask_id=response["qtask_id"], reservation_id=reservation_id)
	queue = qpm.get_queue_metrics()
	capacity = qpm.get_capacity_snapshot()

	assert access_model["methods"]["get_capacity_snapshot"][
		"access_class"] == "manager-aggregate"
	assert capacity_before["active_reservation_count"] == 1
	assert response["outcome"] == "ACCEPTED"
	assert response["lifecycle_state"] == "submitted"
	assert status["scheduler_state"] == "running"
	assert queue["held_capacity"]["qtask_count"] == 1
	assert capacity["held_capacity"]["qtask_count"] == 1

	cancelled = qpm.cancel_task(
		qtask_id=response["qtask_id"],
		reservation_id=reservation_id,
		reason="ops-cancel")
	after_cancel = qpm.get_capacity_snapshot()
	released = qpm.release(reservation_id=reservation_id, reason=7)

	assert cancelled["outcome"] == "CANCELLED"
	assert after_cancel["held_capacity"]["qtask_count"] == 0
	assert released["status"] == "accepted"
	assert qpm.controller.admission_context.released == [(reservation_id, 7)]


def test_completion_queue_created_and_lazily_repaired(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-cq-create")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]

	assert reservation_id in qpm.controller.completion_queues

	qpm.controller.completion_queues.pop(reservation_id)
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})

	assert reservation_id in qpm.controller.completion_queues
	assert response["qtask_id"] in (
		qpm.controller.qtask_ids_by_reservation[reservation_id])


def test_completion_queue_supports_scoped_oldest_peek_and_read(monkeypatch):
	_setup(monkeypatch)
	qpm = CompletingQPM(target_id="ops-cq-scoped")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	first = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	second = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})

	first_peek = qpm.peek_cq(reservation_id=reservation_id)
	second_peek = qpm.peek_cq(reservation_id=reservation_id)
	first_read = qpm.read_cq(reservation_id=reservation_id)
	second_read = qpm.read_cq(reservation_id=reservation_id)

	assert first_peek["cid"] == first["cid"]
	assert second_peek["cid"] == first["cid"]
	assert first_read["cid"] == first["cid"]
	assert second_read["cid"] == second["cid"]
	assert first_read["poll_operation"] == "read_cq"
	assert second_read["completion_ready"] is True


def test_qb_qrc_completion_sink_feeds_reservation_queue(
		monkeypatch, tmp_path):
	_setup(monkeypatch)
	import util.qpm.util_qrc as util_qrc
	from svc_qb_qpm.svc_qrc import QRC as QBQRC

	monkeypatch.setattr(util_qrc, "Event", CompletionEvent)

	class QBQPM(UTIL_QPM):
		def __init__(self):
			self.qb_qrc = QBQRC(start=False)
			super().__init__(
				self.qb_qrc,
				target_id="ops-qb-cq",
				admission_context_factory=FakeAdmissionContext,
				scheduler_context_factory=FakeSchedulerContext,
			)

		def prepare_circuit(self, info):
			info["qfw_backend"] = "qb-hook"
			return info

	class DoneLauncher:
		def status(self, pid):
			return b"", b"", 0

	class WorkerQueue:
		def put(self, item):
			return None

	qpm = QBQPM()
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	request = parse_execution_request({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	qpm.require_managed_execution(request)
	cid = qpm.create_circuit(request.payload, request=request)
	circuit = qpm._prepare_run_circuit(cid, require_selected_cid=True)
	qpm.controller.start_provider_submission(
		circuit, provider_handle=f"provider-{cid}")
	qasm_file = tmp_path / "qb-task.qasm"
	qasm_file.write_text("OPENQASM 2.0;", encoding="utf-8")
	(qasm_file.parent / f"{qasm_file.name}.result").write_text(
		"result: qb-complete\n", encoding="utf-8")
	qpm.qb_qrc.worker_pool = [{
		"active_tasks": [{
			"launcher": DoneLauncher(),
			"pid": 7,
			"circ": circuit,
			"qasm_file": str(qasm_file),
		}],
		"queue": WorkerQueue(),
	}]

	qpm.qb_qrc.check_active_tasks(0)
	completion = qpm.read_cq(cid=cid, reservation_id=reservation_id)

	assert completion["completion_ready"] is True
	assert completion["cid"] == cid
	assert completion["qtask_id"] == circuit.info["qtask_id"]
	assert completion["result"]["result"] == "qb-complete"
	assert qpm.qb_qrc.circuit_results == []


def test_operations_timeout_expiration_and_reconciliation(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-timeout")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	qpm.pause_execution_target(reason="operator")

	timed_out = qpm.sync_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	}, timeout=0)
	cancelled = qpm.cancel_task(
		qtask_id=timed_out["qtask_id"],
		reservation_id=reservation_id,
		reason="timeout-cleanup")

	assert timed_out["outcome"] == "TIMEOUT"
	assert timed_out["lifecycle_state"] == "queued"
	assert cancelled["outcome"] == "CANCELLED"

	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-reconcile")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	qpm.controller.admission_context.reservations[
		reservation_id]["state"] = "expired"

	summary = qpm.reconcile_runtime_state(reason="test-reconciliation")
	telemetry = qpm.get_service_lifecycle_telemetry()
	status = qpm.task_status(
		qtask_id=response["qtask_id"], reservation_id=reservation_id)

	assert summary["capacity_hold_faults"][0]["reason"] == (
		"inactive-reservation-hold")
	assert telemetry["reconciliation_faults"][0]["reservation_state"] == (
		"expired")
	assert status["outcome"] == "FAILED"


def test_operations_reject_reservation_mismatched_task_selectors(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-reservation-scope")
	reservation_a = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	reservation_b = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_a,
	})

	status = qpm.task_status(
		qtask_id=response["qtask_id"], reservation_id=reservation_b)
	metadata = qpm.get_task_metadata(
		task_id=response["qtask_id"], reservation_id=reservation_b)
	cancelled = qpm.cancel_task(qtask_id=response["qtask_id"])
	deleted = qpm.delete_circuit(
		response["cid"], reservation_id=reservation_b)
	read_result = qpm.read_cq(
		cid=response["cid"], reservation_id=reservation_b)

	assert status["outcome"] == "INVALID_RESERVATION"
	assert status["reason"] == "reservation-mismatch"
	assert metadata["outcome"] == "INVALID_RESERVATION"
	assert cancelled["reason"] == "reservation-required"
	assert deleted["reason"] == "reservation-mismatch"
	assert read_result["reason"] == "reservation-mismatch"


def test_event_notifications_are_isolated_by_reservation_and_filters(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-events")
	sinks = {}

	def event_api_factory(class_id=None, target=None):
		sink = CapturingEventAPI(class_id=class_id, target=target)
		sinks[class_id] = sink
		return sink

	monkeypatch.setattr(util_qpm, "BaseEventAPI", event_api_factory)
	reservation_a = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	reservation_b = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	first = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_a,
	})
	second = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_b,
	})

	qpm.register_event_notification(
		"endpoint-a", "circ-result", "class-a",
		reservation_id=reservation_a,
		filters={"qtask_id": first["qtask_id"]})
	qpm.register_event_notification(
		"endpoint-b", "circ-result", "class-b",
		reservation_id=reservation_b,
		filters={"qtask_id": second["qtask_id"]})
	payload = {
		"cid": second["cid"],
		"qtask_id": second["qtask_id"],
	}

	delivered = qpm.controller.dispatch_completion_event(
		CompletionEvent("circ-result", payload))

	assert delivered is True
	assert sinks["class-a"].events == []
	assert sinks["class-b"].events == [payload]
	assert qpm.fake_qrc.push_info["class_id"] == (
		"qpm-completion-event-dispatcher")
	assert len(qpm.controller.event_endpoints["class-a"]) == 1
	assert len(qpm.controller.event_endpoints["class-b"]) == 1


def test_stale_event_endpoint_does_not_block_live_delivery(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-stale-events")
	sinks = {}

	def event_api_factory(class_id=None, target=None):
		if class_id == "class-stale":
			sink = FailingEventAPI(class_id=class_id, target=target)
		else:
			sink = CapturingEventAPI(class_id=class_id, target=target)
		sinks[class_id] = sink
		return sink

	monkeypatch.setattr(util_qpm, "BaseEventAPI", event_api_factory)
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	qpm.register_event_notification(
		"endpoint-stale", "circ-result", "class-stale",
		reservation_id=reservation_id)
	qpm.register_event_notification(
		"endpoint-live", "circ-result", "class-live",
		reservation_id=reservation_id)
	payload = {
		"cid": response["cid"],
		"qtask_id": response["qtask_id"],
	}

	delivered = qpm.controller.dispatch_completion_event(
		CompletionEvent("circ-result", payload))

	assert delivered is True
	assert sinks["class-live"].events == [payload]
	assert "class-stale" not in qpm.controller.event_endpoints
	assert len(qpm.controller.event_endpoints["class-live"]) == 1


def test_completion_events_keep_reservation_scope_after_cleanup(monkeypatch):
	_setup(monkeypatch)
	qpm = CompletingQPM(target_id="ops-events-cleanup")
	sinks = {}

	def event_api_factory(class_id=None, target=None):
		sink = CapturingEventAPI(class_id=class_id, target=target)
		sinks[class_id] = sink
		return sink

	monkeypatch.setattr(util_qpm, "BaseEventAPI", event_api_factory)
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	qpm.register_event_notification(
		"endpoint-a", "circ-result", "class-a",
		reservation_id=reservation_id)

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})

	assert qpm.controller.task_for_cid(response["cid"]) is None
	assert sinks["class-a"].events[0]["cid"] == response["cid"]
	assert sinks["class-a"].events[0]["qtask_id"] == response["qtask_id"]
	peeked = qpm.peek_cq(
		cid=response["cid"], reservation_id=reservation_id)
	read = qpm.read_cq(
		cid=response["cid"], reservation_id=reservation_id)
	assert peeked["cid"] == response["cid"]
	assert read["cid"] == response["cid"]
	assert qpm.fake_qrc.circuit_results == []


def test_result_reads_keep_reservation_scope_after_cleanup(monkeypatch):
	_setup(monkeypatch)
	qpm = CompletingQPM(target_id="ops-results-cleanup")
	reservation_a = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	reservation_b = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_a,
	})

	wrong_peek = qpm.peek_cq(
		cid=response["cid"], reservation_id=reservation_b)
	wrong_read = qpm.read_cq(
		cid=response["cid"], reservation_id=reservation_b)
	wrong_delete = qpm.delete_circuit(
		response["cid"], reservation_id=reservation_b)
	correct_peek = qpm.peek_cq(
		cid=response["cid"], reservation_id=reservation_a)
	correct_read = qpm.read_cq(
		cid=response["cid"], reservation_id=reservation_a)

	assert qpm.controller.task_for_cid(response["cid"]) is None
	assert wrong_peek["reason"] == "reservation-mismatch"
	assert wrong_read["reason"] == "reservation-mismatch"
	assert wrong_delete["reason"] == "reservation-mismatch"
	assert correct_peek["qtask_id"] == response["qtask_id"]
	assert correct_read["qtask_id"] == response["qtask_id"]
	assert response["cid"] not in qpm.controller.terminal_tasks_by_cid
	assert qpm.fake_qrc.circuit_results == []


def test_completion_polling_returns_structured_in_progress_status(
		monkeypatch):
	_setup(monkeypatch)
	qpm = PendingCompletionQPM(target_id="ops-pending-results")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})

	peeked = qpm.peek_cq(cid=response["cid"], reservation_id=reservation_id)
	read = qpm.read_cq(cid=response["cid"], reservation_id=reservation_id)
	empty = qpm.peek_cq()

	assert peeked["completion_ready"] is False
	assert peeked["poll_operation"] == "peek_cq"
	assert peeked["reason"] == "completion-not-ready"
	assert peeked["cid"] == response["cid"]
	assert peeked["qtask_id"] == response["qtask_id"]
	assert peeked["reservation_id"] == reservation_id
	assert peeked["outcome"] == "ACCEPTED"
	assert read["completion_ready"] is False
	assert read["poll_operation"] == "read_cq"
	assert read["cid"] == response["cid"]
	assert qpm.controller.task_for_cid(response["cid"]) is not None
	assert empty == {
		"outcome": "INVALID_RESERVATION",
		"lifecycle_state": "invalid-reservation",
		"reason": "reservation-required",
		"message": (
			"reservation_id is required for managed completion polling"),
		"completion_ready": False,
		"poll_operation": "peek_cq",
	}


def test_completion_polling_without_reservation_rejects_provider_queue(
		monkeypatch):
	_setup(monkeypatch)
	qpm = CompletingQPM(target_id="ops-unscoped-provider-results")
	local_result = {"cid": "provider-local", "result": "local"}
	qpm.fake_qrc.circuit_results.append(local_result)

	read = qpm.read_cq()
	peek = qpm.peek_cq()

	assert read["outcome"] == "INVALID_RESERVATION"
	assert read["reason"] == "reservation-required"
	assert peek["outcome"] == "INVALID_RESERVATION"
	assert peek["reason"] == "reservation-required"
	assert qpm.fake_qrc.circuit_results == [local_result]


def test_completion_polling_rejects_missing_reservation(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-missing-reservation")

	result = qpm.peek_cq(reservation_id="999")

	assert result["outcome"] == "MISSING_RESERVATION"
	assert result["lifecycle_state"] == "missing-reservation"
	assert result["reason"] == "missing-reservation"
	assert result["completion_ready"] is False


def test_failed_provider_completion_is_published_once(monkeypatch):
	_setup(monkeypatch)
	qpm = FailingCompletionQPM(target_id="ops-provider-failure-completion")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]

	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	first = qpm.read_cq(cid=response["cid"], reservation_id=reservation_id)
	second = qpm.read_cq(cid=response["cid"], reservation_id=reservation_id)

	assert first["completion_ready"] is True
	assert first["outcome"] == "FAILED"
	assert first["reason"] == "provider-execution-failed"
	assert first["rc"] == 7
	assert first["result"]["provider_error"] == "async provider failed"
	assert second["completion_ready"] is False
	assert second["reason"] == "completion-not-ready"
	assert qpm.controller.completion_queues[
		reservation_id].dequeued_records == [{
			"cid": response["cid"],
			"qtask_id": response["qtask_id"],
			"reservation_id": reservation_id,
			"dequeue_time_ns": first["qpm_cq_dequeue_time_ns"],
			"operation": "read_cq",
		}]


def test_result_reads_allow_terminal_reservation_state(monkeypatch):
	_setup(monkeypatch)
	qpm = CompletingQPM(target_id="ops-results-terminal-reservation")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})

	released = qpm.release(reservation_id=reservation_id, reason=7)
	status = qpm.task_status(
		qtask_id=response["qtask_id"], reservation_id=reservation_id)
	peeked = qpm.peek_cq(cid=response["cid"], reservation_id=reservation_id)
	read = qpm.read_cq(cid=response["cid"], reservation_id=reservation_id)

	assert released["status"] == "accepted"
	assert qpm.controller.admission_context.reservations[
		reservation_id]["state"] == "released"
	assert status["outcome"] == "COMPLETED"
	assert peeked["cid"] == response["cid"]
	assert read["cid"] == response["cid"]


def test_completion_retention_evicts_records_with_structured_status(
		monkeypatch, tmp_path):
	_setup(monkeypatch)
	_set_retention_site(
		monkeypatch, tmp_path, **{"max-records-per-reservation": 1})
	qpm = CompletingQPM(target_id="ops-retention-records")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	first = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	second = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})

	evicted = qpm.read_cq(
		cid=first["cid"], reservation_id=reservation_id)
	retained = qpm.read_cq(
		cid=second["cid"], reservation_id=reservation_id)

	assert evicted["outcome"] == "NO_LONGER_RETAINED"
	assert evicted["reason"] == "completion-no-longer-retained"
	assert evicted["retention_reason"] == "max-records-exceeded"
	assert retained["cid"] == second["cid"]
	assert retained["completion_ready"] is True


def test_terminal_completion_queue_garbage_collection(monkeypatch, tmp_path):
	_setup(monkeypatch)
	_set_retention_site(monkeypatch, tmp_path, **{
		"terminal-reservation-retention-seconds": 1,
	})
	qpm = CompletingQPM(target_id="ops-retention-terminal")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})

	qpm.release(reservation_id=reservation_id, reason=7)
	terminal_at_ns = (
		qpm.controller.completion_queues[reservation_id].terminal_at_ns)
	summary = qpm.controller.purge_completion_queues(
		now_ns=terminal_at_ns + 1_000_000_000)
	result = qpm.peek_cq(cid=response["cid"], reservation_id=reservation_id)

	assert reservation_id in summary["purged_reservations"]
	assert reservation_id not in qpm.controller.completion_queues
	assert result["outcome"] == "NO_LONGER_RETAINED"
	assert result["retention_reason"] in (
		"terminal-reservation-retention-expired",
		"terminal-reservation-garbage-collected")


def test_completion_purge_worker_expires_idle_terminal_queue(
		monkeypatch, tmp_path):
	_setup(monkeypatch)
	_set_retention_site(monkeypatch, tmp_path, **{
		"terminal-reservation-retention-seconds": 1,
		"purge-interval-seconds": 1,
	})
	qpm = CompletingQPM(target_id="ops-retention-worker")
	reservation_id = qpm.reserve(request={"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	qpm.release(reservation_id=reservation_id, reason=7)
	qpm.controller.completion_queues[
		reservation_id].terminal_at_ns = time.time_ns() - 2_000_000_000

	try:
		deadline = time.time() + 2.5
		while (time.time() < deadline and
				reservation_id in qpm.controller.completion_queues):
			time.sleep(0.05)
		assert reservation_id not in qpm.controller.completion_queues
	finally:
		qpm.controller.stop_completion_purge_worker()

	result = qpm.peek_cq(cid=response["cid"], reservation_id=reservation_id)
	assert result["outcome"] == "NO_LONGER_RETAINED"
	assert result["retention_reason"] in (
		"terminal-reservation-retention-expired",
		"terminal-reservation-garbage-collected")


def test_operations_reject_unmanaged_execution(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-compat")

	for call in (
			lambda: qpm.async_run({
				"qasm": "OPENQASM 2.0;",
				"num_qubits": 2,
			}),
			lambda: qpm.reserve("legacy-service-info"),
			lambda: qpm.release(),
	):
		try:
			call()
		except DEFwExecutionError:
			pass
		else:
			raise AssertionError("expected unmanaged operation rejection")
