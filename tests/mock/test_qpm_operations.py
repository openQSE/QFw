from defw_exception import DEFwExecutionError
from fakes import FakeSchedulerContext
from test_qpm_admission import AdmissionQPM, FakeAdmissionContext, _setup
import util.qpm.util_qpm as util_qpm
from util.qpm.util_qpm import UTIL_QPM


class CapturingEventAPI:
	def __init__(self, class_id=None, target=None):
		self.class_id = class_id
		self.target = target
		self.events = []

	def put(self, event):
		self.events.append(event.get_event())
		return True


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
		circuit.free_resources(circuit)
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


def test_managed_operations_flow_reports_telemetry(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-target")
	reservation_id = qpm.reserve({
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
		"owner": {"user": "ops-user"},
		"job_id": "ops-job",
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
	released = qpm.release(reservation_id, reason=7)

	assert cancelled["outcome"] == "CANCELLED"
	assert after_cancel["held_capacity"]["qtask_count"] == 0
	assert released["status"] == "accepted"
	assert qpm.controller.admission_context.released == [(reservation_id, 7)]


def test_operations_timeout_expiration_and_reconciliation(monkeypatch):
	_setup(monkeypatch)
	qpm = AdmissionQPM(target_id="ops-timeout")
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	qpm.pause(reason="operator")

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
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})
	qpm.controller.admission_context.reservations[
		reservation_id]["state"] = "expired"

	summary = qpm.reconcile_runtime_state(now_ns=456)
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
	reservation_a = qpm.reserve({"num_qubits": 2})["reservation_id"]
	reservation_b = qpm.reserve({"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_a,
	})

	status = qpm.task_status(
		qtask_id=response["qtask_id"], reservation_id=reservation_b)
	metadata = qpm.get_task_metadata(
		cid=response["cid"], reservation_id=reservation_b)
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
	reservation_a = qpm.reserve({"num_qubits": 2})["reservation_id"]
	reservation_b = qpm.reserve({"num_qubits": 2})["reservation_id"]
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


def test_completion_events_keep_reservation_scope_after_cleanup(monkeypatch):
	_setup(monkeypatch)
	qpm = CompletingQPM(target_id="ops-events-cleanup")
	sinks = {}

	def event_api_factory(class_id=None, target=None):
		sink = CapturingEventAPI(class_id=class_id, target=target)
		sinks[class_id] = sink
		return sink

	monkeypatch.setattr(util_qpm, "BaseEventAPI", event_api_factory)
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
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
	assert qpm.fake_qrc.circuit_results == []


def test_result_reads_keep_reservation_scope_after_cleanup(monkeypatch):
	_setup(monkeypatch)
	qpm = CompletingQPM(target_id="ops-results-cleanup")
	reservation_a = qpm.reserve({"num_qubits": 2})["reservation_id"]
	reservation_b = qpm.reserve({"num_qubits": 2})["reservation_id"]
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


def test_result_reads_allow_terminal_reservation_state(monkeypatch):
	_setup(monkeypatch)
	qpm = CompletingQPM(target_id="ops-results-terminal-reservation")
	reservation_id = qpm.reserve({"num_qubits": 2})["reservation_id"]
	response = qpm.async_run({
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 2,
		"reservation_id": reservation_id,
	})

	released = qpm.release(reservation_id, reason=7)
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
