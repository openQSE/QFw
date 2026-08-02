import threading
import time

import util.qpm.util_qpm as util_qpm
from defw_exception import DEFwExecutionError
from fakes import FakeSchedulerContext
from util.qpm.controller import clear_target_controllers
from util.qpm.util_qpm import UTIL_QPM


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

	def __init__(self, threading_mode):
		self.threading = threading_mode
		self.lock = threading.Lock()
		self.requests = []
		self.reservations = {}
		self.authorized = []
		self.consumed = []
		self.returned = []
		self.actual = []
		self.released = []
		self.cancelled = []
		self.expired = []

	def evaluate_request(self, request):
		self.requests.append(("evaluate", dict(request)))
		return self._decision(request, reservation_id=0)

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

	def renew_reservation(self, reservation_id, request):
		return {"status": "accepted", "reservation_id": reservation_id}

	def release_reservation(self, reservation_id, reason_code):
		self.released.append((reservation_id, reason_code))
		self.reservations[reservation_id]["state"] = "released"
		return {"status": "accepted", "reservation_id": reservation_id}

	def cancel_reservation(self, reservation_id, reason_code):
		self.cancelled.append((reservation_id, reason_code))
		self.reservations[reservation_id]["state"] = "cancelled"
		return {"status": "accepted", "reservation_id": reservation_id}

	def expire_reservations(self, now_ns):
		self.expired.append(now_ns)
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

	def record_actual_request(self, reservation_id, actual):
		self.actual.append((reservation_id, dict(actual)))

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
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:2")
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)


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
