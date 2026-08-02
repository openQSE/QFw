import os
import threading
import time
from dataclasses import dataclass, field

from .admission import (
	QHW_ADM_THREAD_SAFE,
	QHW_ADM_THREAD_USER,
	QPMAdmissionPendingCapacity,
	QPMAdmissionValidationError,
	authorize_usage,
	cancel_reservation,
	consume_usage,
	admission_context_available,
	create_admission_context,
	evaluate_request,
	expire_reservations,
	get_reservation,
	list_reservations,
	record_actual,
	register_device_profile,
	release_reservation,
	renew_reservation,
	reserve_request,
	return_usage,
	set_estimator,
	set_policy,
)
from .scheduler import (
	QHW_SCHED_THREAD_SAFE,
	QHW_SCHED_THREAD_USER,
	create_scheduler_context,
	normalize_scheduler_policy,
	mark_scheduler_task_started,
	mark_scheduler_task_completed,
	mark_scheduler_task_failed,
	mark_scheduler_task_cancelled,
	select_next_scheduler_task,
	scheduler_context_available,
	scheduler_task_state_name,
	scheduler_task_count,
	set_scheduler_policy as activate_scheduler_policy,
	submit_scheduler_task,
	QPMSchedulerQueueEmpty,
)


TARGET_ID_ENV = "QFW_QPM_TARGET_ID"
DEVICE_ID_ENV = "QFW_QPU_DEVICE_ID"
ADMISSION_THREADING_ENV = "QFW_QPM_ADMISSION_THREADING_MODE"
SCHEDULER_THREADING_ENV = "QFW_QPM_SCHEDULER_THREADING_MODE"
CONTROLLER_SERIALIZATION_ENV = "QFW_QPM_CONTROLLER_SERIALIZATION_MODE"

DEFAULT_ADMISSION_THREADING_MODE = QHW_ADM_THREAD_SAFE
DEFAULT_SCHEDULER_THREADING_MODE = QHW_SCHED_THREAD_SAFE
DEFAULT_CONTROLLER_SERIALIZATION_MODE = "controller-lock"
QPM_TASK_CREATED = "created"
QPM_TASK_RESOURCES_CONSUMED = "resources-consumed"
QPM_TASK_CAPACITY_HELD = "capacity-held"
QPM_TASK_PENDING_CAPACITY = "pending-capacity"
QPM_TASK_QUEUED = "queued"
QPM_TASK_SELECTED = "selected"
QPM_TASK_SUBMITTED = "submitted"
QPM_TASK_COMPLETED = "completed"
QPM_TASK_FAILED = "failed"
QPM_TASK_CANCELLED = "cancelled"
QPM_TASK_TIMED_OUT = "timed-out"
QPM_TASK_TERMINAL_STATES = (
	QPM_TASK_COMPLETED,
	QPM_TASK_FAILED,
	QPM_TASK_CANCELLED,
)
TELEMETRY_BASIC_DISCOVERY = "basic-discovery"
TELEMETRY_CALLER_OWNED = "caller-owned"
TELEMETRY_MANAGER_AGGREGATE = "manager-aggregate"
TELEMETRY_OPERATOR = "operator"

TELEMETRY_ACCESS_CLASSES = (
	TELEMETRY_BASIC_DISCOVERY,
	TELEMETRY_CALLER_OWNED,
	TELEMETRY_MANAGER_AGGREGATE,
	TELEMETRY_OPERATOR,
)

TELEMETRY_METHOD_LABELS = {
	"get_backend_info": TELEMETRY_BASIC_DISCOVERY,
	"get_device_info": TELEMETRY_BASIC_DISCOVERY,
	"get_dynamic_backend_info": TELEMETRY_BASIC_DISCOVERY,
	"get_calibration_snapshot": TELEMETRY_BASIC_DISCOVERY,
	"get_coupling_graph": TELEMETRY_BASIC_DISCOVERY,
	"get_last_job_timing": TELEMETRY_CALLER_OWNED,
	"get_last_job_metadata": TELEMETRY_CALLER_OWNED,
	"get_task_metadata": TELEMETRY_CALLER_OWNED,
	"get_capacity_snapshot": TELEMETRY_MANAGER_AGGREGATE,
	"get_queue_metrics": TELEMETRY_MANAGER_AGGREGATE,
	"get_service_lifecycle_telemetry": TELEMETRY_OPERATOR,
	"get_scheduler_queue_state": TELEMETRY_MANAGER_AGGREGATE,
	"get_scheduler_status": TELEMETRY_OPERATOR,
	"get_telemetry_access_model": TELEMETRY_BASIC_DISCOVERY,
	"reconcile_runtime_state": TELEMETRY_OPERATOR,
}


@dataclass(frozen=True)
class QPMControllerConfig:
	target_id: str
	admission_threading_mode: str = DEFAULT_ADMISSION_THREADING_MODE
	scheduler_threading_mode: str = DEFAULT_SCHEDULER_THREADING_MODE
	serialization_mode: str = DEFAULT_CONTROLLER_SERIALIZATION_MODE

	def telemetry(self):
		return {
			"target_id": self.target_id,
			"admission_threading_mode": self.admission_threading_mode,
			"scheduler_threading_mode": self.scheduler_threading_mode,
			"serialization_mode": self.serialization_mode,
		}


@dataclass
class QPMRuntimeTask:
	cid: str
	qtask_id: int
	reservation_id: str = None
	scheduler_task_id: object = None
	provider_handle: object = None
	usage_event_id: object = None
	token_metadata: dict = field(default_factory=dict)
	owner_metadata: dict = field(default_factory=dict)
	request_metadata: dict = field(default_factory=dict)
	external_ids: dict = field(default_factory=dict)
	canonical_ids: dict = field(default_factory=dict)
	state: str = QPM_TASK_CREATED


class QPMTargetController:
	def __init__(self, config, admission_context_factory=None,
		     scheduler_context_factory=None):
		self.config = config
		self.lock = threading.RLock()
		self.admission_context = _create_admission_context(
			config, admission_context_factory)
		self.scheduler_context = _create_scheduler_context(
			config, scheduler_context_factory)
		self.binding_count = 0
		self.max_ppn = None
		self.resources_initialized = False
		self.circuits = {}
		self.oor_queue = None
		self.circuit_results = []
		self.free_hosts = {}
		self.all_results = []
		self.push_info = {}
		self.qtask_id_next = 1
		self.runtime_by_cid = {}
		self.runtime_by_qtask_id = {}
		self.qtask_ids_by_reservation = {}
		self.qtask_id_by_scheduler_task_id = {}
		self.qtask_id_by_provider_handle = {}
		self.usage_events_by_qtask_id = {}
		self.pending_capacity = {}
		self.capacity_holds = {}
		self.selected_qtask_ids = set()
		self.provider_inflight = set()
		self.worker_state = {}
		self.event_endpoints = {}
		self.callback_endpoints = {}
		self.provider_canceller = None
		self.timeout_state = {}
		self.result_state = {}
		self.terminal_tasks_by_cid = {}
		self.terminal_tasks_by_qtask_id = {}
		self.audit_records = []
		self.diagnostic_bypass_records = []
		self.lifecycle_events = []
		self.reconciliation_faults = []
		self.external_id_maps = {}
		self.external_id_next = 1
		self.admission_request_id_next = 1
		self.reservation_metadata_by_id = {}
		self.reservation_close_state = {}
		self.device_profile = None
		self.capacity_model = {}
		self.admission_policy = {}
		self.estimator_policy = {}
		self.scheduler_policy = normalize_scheduler_policy(None)
		self.scheduler_control = {
			"paused": False,
			"draining": False,
			"drain_mode": None,
			"drain_timeout_s": None,
			"drain_started_at_ns": None,
			"pause_reason": None,
			"dispatch_depth": 1,
		}
		self.admission_config_versions = {
			"device_profile": 0,
			"capacity_model": 0,
			"admission_policy": 0,
			"estimator_policy": 0,
		}
		self.scheduler_config_versions = {
			"scheduler_policy": 0,
			"scheduler_control": 0,
		}

	def bind(self, max_ppn):
		with self.lock:
			self.binding_count += 1
			if self.max_ppn is None:
				self.max_ppn = max_ppn
			self._record_lifecycle_event_locked(
				"binding-attached",
				details={
					"binding_count": self.binding_count,
					"max_ppn": self.max_ppn,
				})
		return self

	def set_provider_canceller(self, provider_canceller):
		with self.lock:
			self.provider_canceller = provider_canceller

	def telemetry(self):
		info = self.config.telemetry()
		with self.lock:
			info.update({
				"binding_count": self.binding_count,
				"max_ppn": self.max_ppn,
				"resource_hosts": sorted(self.free_hosts.keys()),
				"runtime_task_count": len(self.runtime_by_qtask_id),
				"audit_record_count": len(self.audit_records),
				"diagnostic_bypass_count": len(
					self.diagnostic_bypass_records),
				"lifecycle_event_count": len(self.lifecycle_events),
				"reconciliation_fault_count": len(
					self.reconciliation_faults),
				"admission_context_available": (
					admission_context_available(self.admission_context)),
				"admission_context_threading": getattr(
					self.admission_context, "threading", None),
				"scheduler_context_available": (
					scheduler_context_available(self.scheduler_context)),
				"scheduler_context_threading": getattr(
					self.scheduler_context, "threading", None),
				"admission_config_versions": dict(
					self.admission_config_versions),
				"scheduler_config_versions": dict(
					self.scheduler_config_versions),
				"reservation_metadata_count": len(
					self.reservation_metadata_by_id),
				"closing_reservation_count": len(
					self.reservation_close_state),
				"scheduler_task_count": scheduler_task_count(
					self.scheduler_context),
			})
		return info

	def get_scheduler_status(self):
		with self.lock:
			state = "draining" if self.scheduler_control["draining"] else (
				"paused" if self.scheduler_control["paused"] else "active")
			return {
				"target_id": self.config.target_id,
				"state": state,
				"scheduler_available": scheduler_context_available(
					self.scheduler_context),
				"threading_mode": self.config.scheduler_threading_mode,
				"policy": dict(self.scheduler_policy),
				"control": dict(self.scheduler_control),
				"versions": dict(self.scheduler_config_versions),
				"task_count": scheduler_task_count(self.scheduler_context),
				"pending_capacity_count": len(self.pending_capacity),
				"runtime_task_count": len(self.runtime_by_qtask_id),
				"selected_task_count": len(self.selected_qtask_ids),
				"provider_inflight_count": len(self.provider_inflight),
			}

	def get_scheduler_policy(self):
		with self.lock:
			return {
				"version": (
					self.scheduler_config_versions["scheduler_policy"]),
				"scheduler_policy": dict(self.scheduler_policy),
			}

	def set_scheduler_policy(self, policy):
		with self.lock:
			normalized = activate_scheduler_policy(
				self.scheduler_context, policy)
			self.scheduler_policy = normalized
			version = self._bump_scheduler_config_version(
				"scheduler_policy")
			self._record_lifecycle_event_locked(
				"scheduler-policy-change",
				details={
					"version": version,
					"scheduler_policy": dict(self.scheduler_policy),
				})
			return {
				"status": "accepted",
				"version": version,
				"scheduler_policy": dict(self.scheduler_policy),
			}

	def pause_scheduler(self, reason=None):
		with self.lock:
			self.scheduler_control["paused"] = True
			self.scheduler_control["pause_reason"] = reason
			version = self._bump_scheduler_config_version(
				"scheduler_control")
			self._record_lifecycle_event_locked(
				"scheduler-paused",
				reason=reason,
				details={"version": version})
			return self._scheduler_control_result("paused", version)

	def resume_scheduler(self):
		with self.lock:
			self.scheduler_control["paused"] = False
			self.scheduler_control["draining"] = False
			self.scheduler_control["drain_mode"] = None
			self.scheduler_control["drain_timeout_s"] = None
			self.scheduler_control["drain_started_at_ns"] = None
			self.scheduler_control["pause_reason"] = None
			version = self._bump_scheduler_config_version(
				"scheduler_control")
			self._record_lifecycle_event_locked(
				"scheduler-resumed",
				details={"version": version})
			return self._scheduler_control_result("resumed", version)

	def drain_scheduler(self, mode="graceful", timeout_s=None):
		with self.lock:
			self.scheduler_control["paused"] = True
			self.scheduler_control["draining"] = True
			self.scheduler_control["drain_mode"] = mode
			self.scheduler_control["drain_timeout_s"] = timeout_s
			self.scheduler_control["drain_started_at_ns"] = time.time_ns()
			version = self._bump_scheduler_config_version(
				"scheduler_control")
			self._record_lifecycle_event_locked(
				"scheduler-draining",
				details={
					"version": version,
					"mode": mode,
					"timeout_s": timeout_s,
				})
			return self._scheduler_control_result("draining", version)

	def set_dispatch_depth(self, max_inflight):
		with self.lock:
			depth = int(max_inflight)
			if depth < 1:
				raise ValueError("dispatch depth must be at least 1")
			self.scheduler_control["dispatch_depth"] = depth
			version = self._bump_scheduler_config_version(
				"scheduler_control")
			self._record_lifecycle_event_locked(
				"scheduler-dispatch-depth-change",
				details={
					"version": version,
					"dispatch_depth": depth,
				})
			return self._scheduler_control_result(
				"dispatch-depth-updated", version)

	def get_scheduler_queue_state(self, include_restricted=False):
		with self.lock:
			return {
				"target_id": self.config.target_id,
				"include_restricted": bool(include_restricted),
				"pending_capacity": [
					dict(value, qtask_id=qtask_id)
					for qtask_id, value in self.pending_capacity.items()
				],
				"scheduler_task_count": scheduler_task_count(
					self.scheduler_context),
				"selected_qtask_ids": sorted(self.selected_qtask_ids),
				"provider_inflight_qtask_ids": sorted(
					self.provider_inflight),
				"runtime_tasks": [
					self._task_status_locked(runtime)
					for runtime in self.runtime_by_qtask_id.values()
				],
			}

	def task_status_for_cid(self, cid, outcome=None, reason=None,
				message=None, result=None, reservation_id=None,
				require_reservation=False):
		with self.lock:
			runtime = self.runtime_by_cid.get(cid)
			if runtime is None:
				runtime = self.terminal_tasks_by_cid.get(cid)
			if runtime is None:
				return {
					"outcome": outcome or "UNKNOWN",
					"lifecycle_state": "unknown",
					"cid": cid,
					"reason": reason,
					"message": message,
				}
			error = self._task_reservation_error_locked(
				runtime, reservation_id, require_reservation)
			if error is not None:
				return error
			return self._task_status_locked(
				runtime, outcome=outcome, reason=reason,
				message=message, result=result)

	def task_status_for_qtask_id(self, qtask_id, outcome=None, reason=None,
				     message=None, result=None, reservation_id=None,
				     require_reservation=False):
		with self.lock:
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is None:
				runtime = self.terminal_tasks_by_qtask_id.get(qtask_id)
			if runtime is None:
				return {
					"outcome": outcome or "UNKNOWN",
					"lifecycle_state": "unknown",
					"qtask_id": qtask_id,
					"reason": reason,
					"message": message,
				}
			error = self._task_reservation_error_locked(
				runtime, reservation_id, require_reservation)
			if error is not None:
				return error
			return self._task_status_locked(
				runtime, outcome=outcome, reason=reason,
				message=message, result=result)

	def telemetry_access_model(self):
		return {
			"target_id": self.config.target_id,
			"enforcement": "record-only",
			"access_classes": [
				{
					"name": TELEMETRY_BASIC_DISCOVERY,
					"description": "public backend and device discovery",
				},
				{
					"name": TELEMETRY_CALLER_OWNED,
					"description": "caller-owned task and reservation state",
				},
				{
					"name": TELEMETRY_MANAGER_AGGREGATE,
					"description": "aggregate queue and capacity state",
				},
				{
					"name": TELEMETRY_OPERATOR,
					"description": "operator policy, audit, and health state",
				},
			],
			"methods": {
				name: self._telemetry_method_label(name)
				for name in sorted(TELEMETRY_METHOD_LABELS)
			},
		}

	def capacity_snapshot(self, device_id=None, scope_id=None,
			      access_class=TELEMETRY_MANAGER_AGGREGATE):
		with self.lock:
			now_ns = time.time_ns()
			return {
				"target_id": self.config.target_id,
				"device_id": device_id or self._device_id(),
				"scope_id": scope_id,
				"timestamp_ns": now_ns,
				"access_class": access_class,
				"pending_qtask_count": len(self.pending_capacity),
				"scheduler_queue_depth": scheduler_task_count(
					self.scheduler_context),
				"active_reservation_count": (
					self._active_reservation_count_locked()),
				"held_capacity": self._held_capacity_locked(),
				"in_flight_capacity": self._in_flight_capacity_locked(),
				"available_capacity": self._unavailable_estimate_locked(
					"available-capacity"),
				"estimated_queued_device_time": (
					self._unavailable_estimate_locked(
						"queued-device-time")),
				"scheduler_policy": dict(self.scheduler_policy),
				"device_available": self.resources_initialized,
				"confidence": "observed-controller-state",
				"telemetry": self._telemetry_object_label(
					"capacity-snapshot", access_class),
			}

	def queue_metrics(self, device_id=None,
			  access_class=TELEMETRY_MANAGER_AGGREGATE):
		with self.lock:
			return {
				"target_id": self.config.target_id,
				"device_id": device_id or self._device_id(),
				"timestamp_ns": time.time_ns(),
				"access_class": access_class,
				"pending_qtask_count": len(self.pending_capacity),
				"scheduler_depth": scheduler_task_count(
					self.scheduler_context),
				"active_task_count": len(self.runtime_by_qtask_id),
				"selected_task_count": len(self.selected_qtask_ids),
				"provider_inflight_count": len(self.provider_inflight),
				"held_capacity": self._held_capacity_locked(),
				"in_flight_capacity": self._in_flight_capacity_locked(),
				"policy_state": {
					"scheduler": dict(self.scheduler_policy),
					"admission": dict(self.admission_policy),
				},
				"wait_estimate": self._unavailable_estimate_locked(
					"wait-estimate"),
				"estimated_start": self._unavailable_estimate_locked(
					"start-estimate"),
				"estimated_queued_device_time": (
					self._unavailable_estimate_locked(
						"queued-device-time")),
				"telemetry": self._telemetry_object_label(
					"queue-metrics", access_class),
			}

	def reconcile_runtime_state(self, now_ns=None):
		with self.lock:
			now_ns = now_ns or time.time_ns()
			summary = {
				"target_id": self.config.target_id,
				"timestamp_ns": now_ns,
				"pending_removed": [],
				"stale_runtime_tasks": [],
				"capacity_hold_faults": [],
				"unfinished_scheduler_tasks": [],
				"provider_handle_faults": [],
				"directory_generation_faults": [],
			}
			self._reconcile_pending_capacity_locked(summary)
			self._reconcile_capacity_holds_locked(summary, now_ns)
			self._reconcile_runtime_maps_locked(summary)
			self._record_lifecycle_event_locked(
				"reconciliation",
				details={"summary": dict(summary)})
			return summary

	def service_lifecycle_telemetry(self, access_class=TELEMETRY_OPERATOR):
		with self.lock:
			return {
				"target_id": self.config.target_id,
				"timestamp_ns": time.time_ns(),
				"access_class": access_class,
				"lifecycle_events": [
					dict(record) for record in self.lifecycle_events
				],
				"audit_records": [
					dict(record) for record in self.audit_records
				],
				"diagnostic_bypass_records": [
					dict(record)
					for record in self.diagnostic_bypass_records
				],
				"reconciliation_faults": [
					dict(record)
					for record in self.reconciliation_faults
				],
				"telemetry": self._telemetry_object_label(
					"service-lifecycle", access_class),
			}

	def cancel_task(self, cid=None, qtask_id=None, reason=None,
			reservation_id=None, require_reservation=False):
		with self.lock:
			runtime = self._runtime_for_task_selector_locked(
				cid=cid, qtask_id=qtask_id)
			if runtime is None:
				return {
					"outcome": "UNKNOWN",
					"lifecycle_state": "unknown",
					"cid": cid,
					"qtask_id": qtask_id,
					"reason": "not-found",
				}
			error = self._task_reservation_error_locked(
				runtime, reservation_id, require_reservation)
			if error is not None:
				return error
			if runtime.state == QPM_TASK_COMPLETED:
				return self._task_status_locked(
					runtime, outcome="COMPLETED",
					reason="already-completed")
			if runtime.state == QPM_TASK_CANCELLED:
				return self._task_status_locked(
					runtime, outcome="CANCELLED",
					reason="already-cancelled")
			provider_cancel = (
				self._cancel_provider_for_task_locked(runtime))
			if (provider_cancel is not None and
					not provider_cancel["terminal"]):
				response = self._task_status_locked(
					runtime, outcome="CANCEL_PENDING",
					reason=provider_cancel["reason"],
					message=provider_cancel.get("message"))
				response["provider_cancel_status"] = (
					provider_cancel["status"])
				return response
			self.pending_capacity.pop(runtime.qtask_id, None)
			self._reconcile_task_hold_locked(runtime, self.circuits.get(
				runtime.cid))
			if runtime.scheduler_task_id is not None:
				mark_scheduler_task_cancelled(
					self.scheduler_context, runtime.scheduler_task_id)
			self.selected_qtask_ids.discard(runtime.qtask_id)
			self.provider_inflight.discard(runtime.qtask_id)
			self.result_state.pop(runtime.qtask_id, None)
			self.timeout_state.pop(runtime.qtask_id, None)
			runtime.state = QPM_TASK_CANCELLED
			response = self._task_status_locked(
				runtime, outcome="CANCELLED",
				reason=reason or "cancelled")
			if provider_cancel is not None:
				response["provider_cancel_status"] = (
					provider_cancel["status"])
			return response

	def task_reservation_error(self, cid=None, qtask_id=None,
				   reservation_id=None,
				   require_reservation=False):
		with self.lock:
			runtime = self._runtime_for_task_selector_locked(
				cid=cid, qtask_id=qtask_id)
			if runtime is None:
				runtime = self._terminal_task_for_selector_locked(
					cid=cid, qtask_id=qtask_id)
			if runtime is None:
				return None
			return self._task_reservation_error_locked(
				runtime, reservation_id, require_reservation)

	def register_event_endpoint(self, info):
		registration = dict(info)
		registration["filters"] = dict(info.get("filters") or {})
		class_id = registration.get("class_id")
		with self.lock:
			self.event_endpoints.setdefault(class_id, []).append(
				registration)
			return {
				"status": "accepted",
				"class_id": class_id,
				"registration_count": sum(
					len(items)
					for items in self.event_endpoints.values()),
			}

	def dispatch_completion_event(self, event):
		payload = event.get_event()
		evtype = event.get_evtype()
		with self.lock:
			matches = [
				registration
				for registrations in self.event_endpoints.values()
				for registration in registrations
				if self._event_registration_matches_locked(
					registration, evtype, payload)
			]
		for registration in matches:
			registration["class"].put(event)
		return bool(matches)

	def evaluate_reservation(self, request, token=None):
		with self.lock:
			admission_request = self._admission_request(request, token=token)
			return evaluate_request(self.admission_context, admission_request)

	def reserve_admission(self, request, token=None):
		with self.lock:
			admission_request = self._admission_request(request, token=token)
			decision = reserve_request(self.admission_context, admission_request)
			reservation_id = decision.get("reservation_id")
			if decision.get("status") == "accepted" and reservation_id:
				self.reservation_metadata_by_id[reservation_id] = (
					admission_request["metadata"])
			return decision

	def renew_admission(self, reservation_id, request=None, token=None):
		with self.lock:
			result = renew_reservation(
				self.admission_context, reservation_id, request or {})
			return result

	def release_admission(self, reservation_id, reason_code=0, token=None):
		with self.lock:
			result = self._close_reservation(
				reservation_id, "release", reason_code=reason_code)
			if result.get("status") == "accepted":
				self.reservation_metadata_by_id.pop(reservation_id, None)
			return result

	def cancel_admission(self, reservation_id, reason_code=0, token=None):
		with self.lock:
			result = self._close_reservation(
				reservation_id, "cancel", reason_code=reason_code)
			if result.get("status") == "accepted":
				self.reservation_metadata_by_id.pop(reservation_id, None)
			return result

	def get_admission_reservation(self, reservation_id, token=None):
		with self.lock:
			reservation = get_reservation(self.admission_context, reservation_id)
			metadata = self.reservation_metadata_by_id.get(reservation_id)
			if metadata is not None:
				reservation["request_metadata"] = dict(metadata)
			return reservation

	def list_admission_reservations(self, filters=None, token=None):
		with self.lock:
			reservations = list_reservations(
				self.admission_context, self._admission_filters(filters))
			for reservation in reservations:
				reservation_id = reservation.get("reservation_id")
				metadata = self.reservation_metadata_by_id.get(reservation_id)
				if metadata is not None:
					reservation["request_metadata"] = dict(metadata)
			return reservations

	def validate_reservation_for_context(self, request_context,
					     operation="execution"):
		reservation_id = request_context.reservation_id
		if reservation_id is None:
			raise QPMAdmissionValidationError(
				"reservation_id is required")
		with self.lock:
			reservation = get_reservation(
				self.admission_context, reservation_id)
			if reservation_id in self.reservation_close_state:
				raise QPMAdmissionValidationError(
					f"reservation is closing: "
					f"reservation_id={reservation_id}")
			self._require_reservation_active(reservation, operation)
			self._require_reservation_not_expired(reservation)
			return reservation

	def authorize_capacity_hold(self, circuit):
		qtask_id = circuit.info["qtask_id"]
		runtime = self.task_for_qtask_id(qtask_id)
		if runtime is None or runtime.reservation_id is None:
			raise QPMAdmissionValidationError(
				"reservation-scoped qtask is missing reservation state")
		usage = self._estimated_usage(circuit, runtime)
		with self.lock:
			authorized = authorize_usage(
				self.admission_context, runtime.reservation_id, usage)
			if authorized.get("status") == "accepted":
				committed = consume_usage(
					self.admission_context, runtime.reservation_id,
					usage)
				if committed.get("status") != "accepted":
					runtime.state = QPM_TASK_FAILED
					raise QPMAdmissionValidationError(
						"admission commit failure: "
						f"status={committed.get('status')} "
						f"reason={committed.get('reason')}")
				self.capacity_holds[qtask_id] = {
					"reservation_id": runtime.reservation_id,
					"usage": dict(usage),
					"decision": dict(committed),
				}
				self.record_usage_event(qtask_id, qtask_id)
				runtime.state = QPM_TASK_CAPACITY_HELD
				return committed
			if authorized.get("status") == "delayed":
				self.pending_capacity[qtask_id] = {
					"reservation_id": runtime.reservation_id,
					"cid": runtime.cid,
					"usage": dict(usage),
					"decision": dict(authorized),
				}
				runtime.state = QPM_TASK_PENDING_CAPACITY
				raise QPMAdmissionPendingCapacity(
					"admission usage delayed: "
					f"reservation_id={runtime.reservation_id} "
					f"qtask_id={qtask_id}")
			runtime.state = QPM_TASK_FAILED
			raise QPMAdmissionValidationError(
				"admission usage rejected: "
				f"status={authorized.get('status')} "
				f"reason={authorized.get('reason')}")

	def submit_qtask_to_scheduler(self, circuit):
		qtask_id = circuit.info["qtask_id"]
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			if runtime.scheduler_task_id is not None:
				return runtime
			if qtask_id not in self.capacity_holds:
				raise QPMAdmissionValidationError(
					"scheduler submission requires an active "
					"admission capacity hold")
			scheduler_task_id = submit_scheduler_task(
				self.scheduler_context,
				self._scheduler_task_desc(circuit, runtime))
			runtime.scheduler_task_id = scheduler_task_id
			self.qtask_id_by_scheduler_task_id[scheduler_task_id] = qtask_id
			runtime.state = QPM_TASK_QUEUED
			return runtime

	def select_qtask_for_dispatch(self):
		with self.lock:
			runtime = self._selected_runtime_locked()
			if runtime is not None:
				return runtime
			if not self._can_select_scheduler_task_locked():
				return None
			try:
				assignment = select_next_scheduler_task(
					self.scheduler_context)
			except QPMSchedulerQueueEmpty:
				return None
			scheduler_task_id = assignment["task_id"]
			runtime = self.task_for_scheduler_task_id(scheduler_task_id)
			if runtime is None:
				raise QPMAdmissionValidationError(
					"selected scheduler task is not known to QPM: "
					f"scheduler_task_id={scheduler_task_id}")
			self.selected_qtask_ids.add(runtime.qtask_id)
			runtime.state = QPM_TASK_SELECTED
			return runtime

	def start_provider_submission(self, circuit, provider_handle=None):
		qtask_id = circuit.info["qtask_id"]
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			if runtime.scheduler_task_id is not None:
				mark_scheduler_task_started(
					self.scheduler_context, runtime.scheduler_task_id)
			self.selected_qtask_ids.discard(qtask_id)
			self.provider_inflight.add(qtask_id)
			if provider_handle is not None:
				runtime.provider_handle = provider_handle
				self.qtask_id_by_provider_handle[provider_handle] = qtask_id
			runtime.state = QPM_TASK_SUBMITTED
			return runtime

	def retry_pending_capacity(self, reservation_id=None):
		results = []
		with self.lock:
			pending_items = list(self.pending_capacity.items())
			for qtask_id, pending in pending_items:
				if (reservation_id is not None and
						pending["reservation_id"] != reservation_id):
					continue
				runtime = self.runtime_by_qtask_id.get(qtask_id)
				if runtime is None:
					self.pending_capacity.pop(qtask_id, None)
					continue
				try:
					self._require_pending_capacity_reservation_valid_locked(
						qtask_id, pending, runtime)
				except QPMAdmissionValidationError as error:
					results.append(
						self._reject_pending_capacity_locked(
							qtask_id, pending, runtime, error))
					continue
				usage = dict(pending["usage"])
				authorized = authorize_usage(
					self.admission_context, pending["reservation_id"],
					usage)
				if authorized.get("status") == "delayed":
					pending["decision"] = dict(authorized)
					results.append({
						"qtask_id": qtask_id,
						"status": "delayed",
						"decision": dict(authorized),
					})
					continue
				if authorized.get("status") != "accepted":
					runtime.state = QPM_TASK_FAILED
					self.pending_capacity.pop(qtask_id, None)
					results.append({
						"qtask_id": qtask_id,
						"status": "rejected",
						"decision": dict(authorized),
					})
					continue
				committed = consume_usage(
					self.admission_context, pending["reservation_id"],
					usage)
				if committed.get("status") != "accepted":
					runtime.state = QPM_TASK_FAILED
					self.pending_capacity.pop(qtask_id, None)
					results.append({
						"qtask_id": qtask_id,
						"status": "commit-failed",
						"decision": dict(committed),
					})
					continue
				self.pending_capacity.pop(qtask_id, None)
				self.capacity_holds[qtask_id] = {
					"reservation_id": pending["reservation_id"],
					"usage": usage,
					"decision": dict(committed),
				}
				self.record_usage_event(qtask_id, qtask_id)
				runtime.state = QPM_TASK_CAPACITY_HELD
				results.append({
					"qtask_id": qtask_id,
					"status": "accepted",
					"decision": dict(committed),
				})
		return results

	def _require_pending_capacity_reservation_valid_locked(
			self, qtask_id, pending, runtime):
		reservation_id = pending.get("reservation_id")
		if reservation_id is None:
			raise QPMAdmissionValidationError(
				f"pending capacity is missing reservation_id: "
				f"qtask_id={qtask_id}")
		if runtime.reservation_id != reservation_id:
			raise QPMAdmissionValidationError(
				"pending capacity reservation mismatch: "
				f"qtask_id={qtask_id} "
				f"pending_reservation_id={reservation_id} "
				f"runtime_reservation_id={runtime.reservation_id}")
		if reservation_id in self.reservation_close_state:
			raise QPMAdmissionValidationError(
				f"reservation is closing: reservation_id={reservation_id}")
		try:
			reservation = get_reservation(
				self.admission_context, reservation_id)
		except Exception as error:
			raise QPMAdmissionValidationError(
				f"reservation lookup failed: reservation_id={reservation_id} "
				f"error={error}") from error
		self._require_reservation_active(
			reservation, "pending capacity retry")
		self._require_reservation_not_expired(reservation)
		return reservation

	def _reject_pending_capacity_locked(self, qtask_id, pending, runtime,
					    error):
		self.pending_capacity.pop(qtask_id, None)
		if runtime.state not in QPM_TASK_TERMINAL_STATES:
			runtime.state = QPM_TASK_FAILED
		return {
			"qtask_id": qtask_id,
			"status": "rejected",
			"decision": {
				"status": "rejected",
				"reason": "invalid-reservation",
				"reservation_id": pending.get("reservation_id"),
				"message": str(error),
			},
		}

	def close_expired_reservation(self, reservation_id, now_ns=None):
		with self.lock:
			return self._close_reservation(
				reservation_id, "expire", now_ns=now_ns or time.time_ns())

	def configure_device_profile(self, profile=None):
		with self.lock:
			normalized = self._normalize_device_profile(profile or {})
			register_device_profile(self.admission_context, normalized)
			self.device_profile = normalized
			version = self._bump_admission_config_version("device_profile")
			return {
				"status": "accepted",
				"version": version,
				"device_profile": dict(self.device_profile),
			}

	def get_device_profile(self):
		with self.lock:
			return {
				"version": self.admission_config_versions["device_profile"],
				"device_profile": (
					dict(self.device_profile)
					if self.device_profile is not None else None),
			}

	def set_capacity_model(self, capacity_model):
		with self.lock:
			self.capacity_model = dict(capacity_model or {})
			version = self._bump_admission_config_version("capacity_model")
			return {
				"status": "accepted",
				"version": version,
				"capacity_model": dict(self.capacity_model),
			}

	def get_capacity_model(self):
		with self.lock:
			return {
				"version": self.admission_config_versions["capacity_model"],
				"capacity_model": dict(self.capacity_model),
			}

	def set_admission_policy(self, policy):
		with self.lock:
			self.admission_policy = dict(policy or {})
			device_id = self._device_id()
			set_policy(self.admission_context, device_id, self.admission_policy)
			version = self._bump_admission_config_version("admission_policy")
			return {
				"status": "accepted",
				"version": version,
				"admission_policy": dict(self.admission_policy),
			}

	def get_admission_policy(self):
		with self.lock:
			return {
				"version": self.admission_config_versions["admission_policy"],
				"admission_policy": dict(self.admission_policy),
			}

	def set_estimator_policy(self, estimator):
		with self.lock:
			self.estimator_policy = dict(estimator or {})
			device_id = self._device_id()
			set_estimator(
				self.admission_context, device_id, self.estimator_policy)
			version = self._bump_admission_config_version("estimator_policy")
			return {
				"status": "accepted",
				"version": version,
				"estimator_policy": dict(self.estimator_policy),
			}

	def get_estimator_policy(self):
		with self.lock:
			return {
				"version": self.admission_config_versions["estimator_policy"],
				"estimator_policy": dict(self.estimator_policy),
			}

	def register_circuit(self, cid, request_context, payload):
		with self.lock:
			qtask_id = self.allocate_qtask_id()
			payload["qtask_id"] = qtask_id
			reservation_metadata = self._reservation_metadata_for_id_locked(
				request_context.reservation_id)
			owner_metadata = reservation_metadata.get("owner", {})
			if not isinstance(owner_metadata, dict):
				owner_metadata = {}
			runtime = QPMRuntimeTask(
				cid=cid,
				qtask_id=qtask_id,
				reservation_id=request_context.reservation_id,
				token_metadata=_token_metadata(request_context.token),
				owner_metadata=dict(owner_metadata),
				request_metadata=dict(reservation_metadata),
			)
			runtime.external_ids, runtime.canonical_ids = (
				self.canonicalize_reservation_metadata(reservation_metadata))
			self.terminal_tasks_by_cid.pop(cid, None)
			self.runtime_by_cid[cid] = runtime
			self.runtime_by_qtask_id[qtask_id] = runtime
			if runtime.reservation_id is not None:
				self.qtask_ids_by_reservation.setdefault(
					runtime.reservation_id, set()).add(qtask_id)
			return runtime

	def allocate_qtask_id(self):
		qtask_id = self.qtask_id_next
		self.qtask_id_next += 1
		return qtask_id

	def canonicalize_reservation_metadata(self, metadata):
		external_ids = {}
		canonical_ids = {}
		owner = metadata.get("owner", {})
		if not isinstance(owner, dict):
			owner = {}
		owner_id = metadata.get("external_user_id") or _owner_identifier(owner)
		for kind, value in (
			("owner_id", owner_id),
			("job_id", metadata.get("external_job_id")),
			("allocation_id", metadata.get("external_allocation_id")),
			("project_id", metadata.get("external_project_id")),
			("session_id", metadata.get("external_session_id")),
		):
			if value is None:
				continue
			external_ids[kind] = value
			canonical_ids[kind] = self.canonicalize_external_id(kind, value)
		return external_ids, canonical_ids

	def canonicalize_external_id(self, kind, value):
		if isinstance(value, int) and not isinstance(value, bool):
			return value

		key = str(value)
		ids_by_value = self.external_id_maps.setdefault(kind, {})
		if key not in ids_by_value:
			ids_by_value[key] = self.external_id_next
			self.external_id_next += 1
		return ids_by_value[key]

	def task_for_cid(self, cid):
		with self.lock:
			return self.runtime_by_cid.get(cid)

	def task_for_qtask_id(self, qtask_id):
		with self.lock:
			return self.runtime_by_qtask_id.get(qtask_id)

	def task_for_scheduler_task_id(self, scheduler_task_id):
		with self.lock:
			qtask_id = self.qtask_id_by_scheduler_task_id.get(scheduler_task_id)
			if qtask_id is None:
				return None
			return self.runtime_by_qtask_id.get(qtask_id)

	def task_for_provider_handle(self, provider_handle):
		with self.lock:
			qtask_id = self.qtask_id_by_provider_handle.get(provider_handle)
			if qtask_id is None:
				return None
			return self.runtime_by_qtask_id.get(qtask_id)

	def forget_terminal_task_for_cid(self, cid):
		with self.lock:
			runtime = self.terminal_tasks_by_cid.pop(cid, None)
			if runtime is not None:
				self.terminal_tasks_by_qtask_id.pop(
					runtime.qtask_id, None)
			return runtime

	def bind_scheduler_task(self, qtask_id, scheduler_task_id):
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			runtime.scheduler_task_id = scheduler_task_id
			self.qtask_id_by_scheduler_task_id[scheduler_task_id] = qtask_id
			return runtime

	def bind_provider_handle(self, qtask_id, provider_handle):
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			runtime.provider_handle = provider_handle
			self.qtask_id_by_provider_handle[provider_handle] = qtask_id
			return runtime

	def record_usage_event(self, qtask_id, usage_event_id):
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			runtime.usage_event_id = usage_event_id
			self.usage_events_by_qtask_id[qtask_id] = usage_event_id
			return runtime

	def set_task_state(self, qtask_id, state):
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			runtime.state = state
			return runtime

	def record_result(self, qtask_id, result):
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			self.result_state[qtask_id] = result
			self.timeout_state.pop(qtask_id, None)
			runtime.state = QPM_TASK_COMPLETED
			return runtime

	def complete_scheduled_task(self, circuit, result=None):
		qtask_id = circuit.info["qtask_id"]
		with self.lock:
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is None:
				return None
			if runtime.state in (QPM_TASK_COMPLETED, QPM_TASK_CANCELLED):
				return runtime
			self._reconcile_task_hold_locked(runtime, circuit)
			if runtime.scheduler_task_id is not None:
				mark_scheduler_task_completed(
					self.scheduler_context, runtime.scheduler_task_id)
			self.provider_inflight.discard(qtask_id)
			self.selected_qtask_ids.discard(qtask_id)
			self.result_state[qtask_id] = result
			self.timeout_state.pop(qtask_id, None)
			runtime.state = QPM_TASK_COMPLETED
			return runtime

	def fail_scheduled_task(self, circuit, error=None):
		qtask_id = circuit.info["qtask_id"]
		with self.lock:
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is None:
				return None
			if runtime.state in (
					QPM_TASK_FAILED, QPM_TASK_CANCELLED,
					QPM_TASK_COMPLETED):
				return runtime
			self._reconcile_task_hold_locked(runtime, circuit)
			if runtime.scheduler_task_id is not None:
				mark_scheduler_task_failed(
					self.scheduler_context, runtime.scheduler_task_id)
			self.provider_inflight.discard(qtask_id)
			self.selected_qtask_ids.discard(qtask_id)
			self.timeout_state.pop(qtask_id, None)
			runtime.state = QPM_TASK_FAILED
			if error is not None:
				self.result_state[qtask_id] = {
					"error": str(error),
					"error_type": type(error).__name__,
				}
			return runtime

	def record_timeout(self, qtask_id, reason=None, message=None):
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			self.timeout_state[qtask_id] = {
				"reason": reason,
				"message": message,
				"timestamp_ns": time.time_ns(),
			}
			return runtime

	def cleanup_circuit(self, cid):
		with self.lock:
			runtime = self.runtime_by_cid.get(cid)
			if runtime is None:
				return None
			return self.cleanup_task(runtime.qtask_id)

	def cleanup_task(self, qtask_id):
		with self.lock:
			runtime = self.runtime_by_qtask_id.pop(qtask_id, None)
			if runtime is None:
				return None
			self._retain_terminal_task_locked(runtime)
			self.runtime_by_cid.pop(runtime.cid, None)
			if runtime.reservation_id in self.qtask_ids_by_reservation:
				qtask_ids = self.qtask_ids_by_reservation[runtime.reservation_id]
				qtask_ids.discard(qtask_id)
				if not qtask_ids:
					self.qtask_ids_by_reservation.pop(
						runtime.reservation_id, None)
			if runtime.scheduler_task_id is not None:
				self.qtask_id_by_scheduler_task_id.pop(
					runtime.scheduler_task_id, None)
			if runtime.provider_handle is not None:
				self.qtask_id_by_provider_handle.pop(
					runtime.provider_handle, None)
			self.usage_events_by_qtask_id.pop(qtask_id, None)
			self.pending_capacity.pop(qtask_id, None)
			self.capacity_holds.pop(qtask_id, None)
			self.selected_qtask_ids.discard(qtask_id)
			self.provider_inflight.discard(qtask_id)
			self.timeout_state.pop(qtask_id, None)
			self.result_state.pop(qtask_id, None)
			return runtime

	def _retain_terminal_task_locked(self, runtime):
		snapshot = QPMRuntimeTask(
			cid=runtime.cid,
			qtask_id=runtime.qtask_id,
			reservation_id=runtime.reservation_id,
			scheduler_task_id=runtime.scheduler_task_id,
			provider_handle=runtime.provider_handle,
			usage_event_id=runtime.usage_event_id,
			token_metadata=dict(runtime.token_metadata),
			owner_metadata=dict(runtime.owner_metadata),
			request_metadata=dict(runtime.request_metadata),
			external_ids=dict(runtime.external_ids),
			canonical_ids=dict(runtime.canonical_ids),
			state=runtime.state,
		)
		self.terminal_tasks_by_cid[snapshot.cid] = snapshot
		self.terminal_tasks_by_qtask_id[snapshot.qtask_id] = snapshot
		return snapshot

	def record_diagnostic_bypass(self, operation, request_context,
				     reason=None):
		record = {
			"operation": operation,
			"reason": reason,
			"target_id": self.config.target_id,
			"reservation_id": request_context.reservation_id,
			"token_metadata": _token_metadata(request_context.token),
			"owner_metadata": {},
			"auth_disabled": request_context.auth_disabled,
			"timestamp": time.time(),
		}
		with self.lock:
			self.audit_records.append(record)
			self.diagnostic_bypass_records.append(record)
			self._record_lifecycle_event_locked(
				"diagnostic-bypass",
				reason=reason,
				details={"operation": operation})
		return record

	def _normalize_device_profile(self, profile):
		device_id = profile.get("device_id", self._device_id())
		max_qubits = profile.get("max_qubits", 0)
		baseline = dict(profile.get("baseline", {}))
		if not baseline:
			baseline = {
				"qubit_count": max(1, min(max_qubits or 1, 4)),
				"depth": 1,
				"one_q_gate_count": 0,
				"two_q_gate_count": 0,
				"shots": 1,
				"measurement_count": 1,
			}
		normalized = dict(profile)
		normalized["device_id"] = device_id
		normalized.setdefault("external_device_id", self.config.target_id)
		normalized.setdefault("baseline", baseline)
		normalized.setdefault("max_shots", 1)
		normalized.setdefault("one_q_gate_ns", 1)
		normalized.setdefault("two_q_gate_ns", 1)
		normalized.setdefault("measurement_ns", 1)
		normalized.setdefault("default_ttl_ns", 60_000_000_000)
		return normalized

	def _device_id(self):
		if self.device_profile is not None:
			return self.device_profile["device_id"]
		return self.canonicalize_external_id("device_id", self.config.target_id)

	def _bump_admission_config_version(self, key):
		self.admission_config_versions[key] += 1
		return self.admission_config_versions[key]

	def _bump_scheduler_config_version(self, key):
		self.scheduler_config_versions[key] += 1
		return self.scheduler_config_versions[key]

	def _scheduler_control_result(self, status, version):
		return {
			"status": status,
			"version": version,
			"target_id": self.config.target_id,
			"control": dict(self.scheduler_control),
		}

	def _task_status_locked(self, runtime, outcome=None, reason=None,
				message=None, result=None):
		timeout = self.timeout_state.get(runtime.qtask_id)
		queue_observation = self._queue_observation_locked(runtime)
		response = {
			"outcome": outcome or self._task_outcome_locked(runtime),
			"lifecycle_state": runtime.state,
			"cid": runtime.cid,
			"qtask_id": runtime.qtask_id,
			"reservation_id": runtime.reservation_id,
			"scheduler_task_id": runtime.scheduler_task_id,
			"provider_handle": runtime.provider_handle,
			"state": runtime.state,
		}
		scheduler_state = self._scheduler_state_locked(runtime)
		if scheduler_state is not None:
			response["scheduler_state"] = scheduler_state
		response.update(queue_observation)
		if reason is not None:
			response["reason"] = reason
		if message is not None:
			response["message"] = message
		if result is not None:
			response["result"] = result
		if timeout is not None:
			response["timeout"] = dict(timeout)
		response["telemetry"] = {
			"access_class": TELEMETRY_CALLER_OWNED,
			"object": "managed-qtask",
			"field_visibility": self._task_status_field_visibility(),
		}
		return {key: value for key, value in response.items()
			if value is not None}

	def _task_outcome_locked(self, runtime):
		if runtime.state == QPM_TASK_COMPLETED:
			return "COMPLETED"
		if runtime.state == QPM_TASK_FAILED:
			return "FAILED"
		if runtime.state == QPM_TASK_CANCELLED:
			return "CANCELLED"
		if runtime.state == QPM_TASK_PENDING_CAPACITY:
			return "DELAYED"
		return "ACCEPTED"

	def _runtime_for_task_selector_locked(self, cid=None, qtask_id=None):
		if qtask_id is not None:
			return self.runtime_by_qtask_id.get(qtask_id)
		if cid is not None:
			return self.runtime_by_cid.get(cid)
		return None

	def _terminal_task_for_selector_locked(self, cid=None, qtask_id=None):
		if qtask_id is not None:
			return self.terminal_tasks_by_qtask_id.get(qtask_id)
		if cid is not None:
			return self.terminal_tasks_by_cid.get(cid)
		return None

	def _task_reservation_error_locked(self, runtime, reservation_id,
					   require_reservation):
		if (reservation_id is None and require_reservation and
				runtime.reservation_id is not None):
			return self._invalid_task_reservation_locked(
				runtime, reservation_id,
				"reservation-required",
				"reservation_id is required for task operation")
		if runtime.reservation_id == reservation_id:
			return None
		if runtime.reservation_id is None and reservation_id is None:
			return None
		return self._invalid_task_reservation_locked(
			runtime, reservation_id, "reservation-mismatch",
			"task does not belong to the supplied reservation")

	def _invalid_task_reservation_locked(self, runtime, reservation_id,
					     reason, message):
		return {
			"outcome": "INVALID_RESERVATION",
			"lifecycle_state": "invalid-reservation",
			"cid": runtime.cid,
			"qtask_id": runtime.qtask_id,
			"reservation_id": reservation_id,
			"reason": reason,
			"message": message,
		}

	def _event_registration_matches_locked(self, registration, evtype,
					       payload):
		if registration.get("evtype") != evtype:
			return False
		cid = payload.get("cid") if isinstance(payload, dict) else None
		qtask_id = (
			payload.get("qtask_id") if isinstance(payload, dict) else None)
		runtime = self._runtime_for_task_selector_locked(
			cid=cid, qtask_id=qtask_id)
		if runtime is None:
			runtime = self._terminal_task_for_selector_locked(
				cid=cid, qtask_id=qtask_id)
		reservation_id = registration.get("reservation_id")
		if reservation_id is not None:
			if runtime is None:
				return False
			if runtime.reservation_id != reservation_id:
				return False
		return self._event_filters_match_locked(
			registration.get("filters", {}), payload, runtime)

	def _event_filters_match_locked(self, filters, payload, runtime):
		for key, expected in filters.items():
			actual = _event_filter_value(payload, runtime, key)
			if not _filter_value_matches(expected, actual):
				return False
		return True

	def _queue_observation_locked(self, runtime):
		observation = {
			"wait_estimate": {
				"available": False,
				"reason": "telemetry-unavailable",
			},
		}
		if runtime.qtask_id in self.pending_capacity:
			pending_ids = list(self.pending_capacity.keys())
			observation["pending_queue_position"] = (
				pending_ids.index(runtime.qtask_id) + 1)
			return observation
		if runtime.state in (QPM_TASK_QUEUED, QPM_TASK_SELECTED):
			observation["scheduler_queue_position"] = None
			observation["scheduling_order"] = None
		return observation

	def _scheduler_state_locked(self, runtime):
		if runtime.scheduler_task_id is None:
			return None
		try:
			return scheduler_task_state_name(
				self.scheduler_context, runtime.scheduler_task_id)
		except Exception:
			return None

	def _reconcile_pending_capacity_locked(self, summary):
		for qtask_id, pending in list(self.pending_capacity.items()):
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is not None and runtime.cid in self.circuits:
				continue
			reason = "missing-runtime" if runtime is None else "missing-circuit"
			summary["pending_removed"].append({
				"qtask_id": qtask_id,
				"reservation_id": pending.get("reservation_id"),
				"reason": reason,
			})
			if runtime is None:
				self.pending_capacity.pop(qtask_id, None)
				continue
			self.cleanup_task(qtask_id)

	def _reconcile_capacity_holds_locked(self, summary, now_ns):
		for qtask_id, hold in list(self.capacity_holds.items()):
			reservation_id = hold["reservation_id"]
			reservation_state, lookup_error = (
				self._reservation_lifecycle_state_locked(reservation_id))
			if reservation_state == "active":
				continue
			fault = {
				"qtask_id": qtask_id,
				"reservation_id": reservation_id,
				"reservation_state": reservation_state or "unknown",
				"timestamp_ns": now_ns,
			}
			if lookup_error is not None:
				fault["reason"] = "reservation-lookup-failed"
				fault["lookup_error"] = lookup_error
				record = self._record_reconciliation_fault_locked(fault)
				summary["capacity_hold_faults"].append(dict(record))
				continue
			fault["reason"] = "inactive-reservation-hold"
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is not None and runtime.scheduler_task_id is not None:
				try:
					mark_scheduler_task_failed(
						self.scheduler_context, runtime.scheduler_task_id)
				except Exception as error:
					fault["scheduler_error"] = str(error)
			self.capacity_holds.pop(qtask_id, None)
			self.usage_events_by_qtask_id.pop(qtask_id, None)
			if runtime is not None:
				runtime.state = QPM_TASK_FAILED
				self.selected_qtask_ids.discard(qtask_id)
				self.provider_inflight.discard(qtask_id)
			record = self._record_reconciliation_fault_locked(fault)
			summary["capacity_hold_faults"].append(dict(record))

	def _reconcile_runtime_maps_locked(self, summary):
		for qtask_id, runtime in list(self.runtime_by_qtask_id.items()):
			if (runtime.cid not in self.circuits and
					runtime.state in QPM_TASK_TERMINAL_STATES):
				summary["stale_runtime_tasks"].append({
					"qtask_id": qtask_id,
					"cid": runtime.cid,
					"state": runtime.state,
				})
				self.cleanup_task(qtask_id)
				continue
			if (runtime.scheduler_task_id is not None and
					runtime.state not in QPM_TASK_TERMINAL_STATES):
				summary["unfinished_scheduler_tasks"].append({
					"qtask_id": qtask_id,
					"scheduler_task_id": runtime.scheduler_task_id,
					"scheduler_state": self._scheduler_state_locked(runtime),
					"lifecycle_state": runtime.state,
				})
			if (runtime.provider_handle is not None and
					qtask_id not in self.provider_inflight and
					runtime.state not in QPM_TASK_TERMINAL_STATES):
				summary["provider_handle_faults"].append({
					"qtask_id": qtask_id,
					"provider_handle": runtime.provider_handle,
					"lifecycle_state": runtime.state,
				})

	def _reservation_lifecycle_state_locked(self, reservation_id):
		try:
			reservation = get_reservation(
				self.admission_context, reservation_id)
		except Exception as error:
			return None, str(error)
		return reservation.get("state"), None

	def _record_lifecycle_event_locked(self, event, reason=None, details=None):
		record = {
			"event": event,
			"target_id": self.config.target_id,
			"timestamp_ns": time.time_ns(),
		}
		if reason is not None:
			record["reason"] = reason
		if details is not None:
			record["details"] = dict(details)
		self.lifecycle_events.append(record)
		self.audit_records.append(record)
		return record

	def _record_reconciliation_fault_locked(self, fault):
		record = dict(fault)
		record.setdefault("event", "reconciliation-fault")
		record.setdefault("target_id", self.config.target_id)
		record.setdefault("timestamp_ns", time.time_ns())
		self.reconciliation_faults.append(record)
		self.audit_records.append(record)
		return record

	def _active_reservation_count_locked(self):
		try:
			reservations = list_reservations(self.admission_context, {})
		except Exception:
			return None
		return sum(1 for reservation in reservations
			if reservation.get("state") == "active")

	def _held_capacity_locked(self):
		return self._usage_totals_locked(self.capacity_holds.values())

	def _in_flight_capacity_locked(self):
		holds = [
			self.capacity_holds[qtask_id]
			for qtask_id in self.provider_inflight
			if qtask_id in self.capacity_holds
		]
		return self._usage_totals_locked(holds)

	def _usage_totals_locked(self, holds):
		totals = {
			"qtask_count": 0,
			"estimated_ns": 0,
			"baseline_units": 0,
			"credits": 0,
			"rate_units": 0,
		}
		for hold in holds:
			usage = hold.get("usage", {})
			totals["qtask_count"] += 1
			for field in (
					"estimated_ns", "baseline_units",
					"credits", "rate_units"):
				totals[field] += usage.get(field, 0)
		return totals

	def _unavailable_estimate_locked(self, kind):
		return {
			"available": False,
			"kind": kind,
			"reason": "telemetry-unavailable",
			"timestamp_ns": time.time_ns(),
			"confidence": "unavailable",
			"policy_context": {
				"scheduler_policy": dict(self.scheduler_policy),
			},
		}

	def _telemetry_object_label(self, object_name, access_class):
		return {
			"object": object_name,
			"access_class": access_class,
			"enforced": False,
		}

	def _telemetry_method_label(self, method_name):
		access_class = TELEMETRY_METHOD_LABELS[method_name]
		return {
			"access_class": access_class,
			"enforced": False,
			"object_visibility": access_class,
			"field_visibility": {
				"telemetry": TELEMETRY_BASIC_DISCOVERY,
				"data": access_class,
			},
		}

	def _task_status_field_visibility(self):
		return {
			"cid": TELEMETRY_CALLER_OWNED,
			"qtask_id": TELEMETRY_CALLER_OWNED,
			"reservation_id": TELEMETRY_CALLER_OWNED,
			"scheduler_task_id": TELEMETRY_CALLER_OWNED,
			"provider_handle": TELEMETRY_OPERATOR,
			"scheduler_state": TELEMETRY_MANAGER_AGGREGATE,
			"wait_estimate": TELEMETRY_MANAGER_AGGREGATE,
			"timeout": TELEMETRY_CALLER_OWNED,
		}

	def _admission_request(self, request, token=None):
		request = dict(request or {})
		owner = dict(request.get("owner", {}))
		policy = dict(request.get("policy", {}))
		workload = dict(request.get("workload", {}))
		run_context = dict(request.get("run_context", {}))
		operation = (
			request.get("operation") or
			request.get("operation_type") or
			run_context.get("operation") or
			workload.get("operation"))
		device_external = (
			request.get("target_device_id") or
			request.get("device_id") or
			self.config.target_id)
		scope_external = request.get("scope_id", 0)
		user_external = (
			request.get("user_id") or
			_owner_identifier(owner) or
			"anonymous")
		job_external = (
			request.get("job_id") or
			request.get("allocation_id") or
			0)
		device_id = _numeric_or_canonical(
			self, "device_id", request.get("device_numeric_id"),
			device_external)
		scope_id = _numeric_or_canonical(
			self, "scope_id", request.get("scope_numeric_id"),
			scope_external)
		user_id = _numeric_or_canonical(
			self, "user_id", request.get("user_numeric_id"), user_external)
		job_id = _numeric_or_canonical(
			self, "job_id", request.get("job_numeric_id"), job_external)
		task_class = self._admission_task_class(request)
		return {
			"request_id": request.get(
				"request_id", self._allocate_admission_request_id()),
			"device_id": device_id,
			"user_id": user_id,
			"job_id": job_id,
			"scope_id": scope_id,
			"reservation_id": request.get("reservation_id", 0),
			"workload_kind": request.get("workload_kind", "quantum"),
			"walltime_ns": request.get("walltime_ns", 0),
			"ttl_ns": request.get("ttl_ns", request.get("expiration_ttl_ns", 0)),
			"classical_runtime_ns": request.get("classical_runtime_ns", 0),
			"overhead_ns": request.get("overhead_ns", 0),
			"priority": request.get("priority", 0),
			"task_class": task_class,
			"metadata": {
				"owner": owner,
				"token_present": token is not None,
				"external_device_id": device_external,
				"external_scope_id": scope_external,
				"external_user_id": user_external,
				"external_job_id": job_external,
				"external_allocation_id": request.get("allocation_id"),
				"external_project_id": request.get("project_id"),
				"external_session_id": request.get("session_id"),
				"operation": operation,
				"policy": policy,
				"workload": workload,
				"run_context": run_context,
			},
		}

	def _admission_task_class(self, request):
		task_class = dict(request.get("task_class", {}))
		shots = (
			task_class.get("shots") or
			request.get("shots") or
			request.get("num_shots") or
			1)
		qubits = (
			task_class.get("qubit_count") or
			request.get("qubit_count") or
			request.get("num_qubits") or
			1)
		return {
			"class_id": task_class.get("class_id", 1),
			"count": task_class.get("count", 1),
			"qubit_count": qubits,
			"depth": task_class.get("depth", request.get("depth", 1)),
			"one_q_gate_count": task_class.get("one_q_gate_count", 0),
			"two_q_gate_count": task_class.get("two_q_gate_count", 0),
			"shots": shots,
			"measurement_count": task_class.get("measurement_count", 1),
		}

	def _admission_filters(self, filters):
		filters = dict(filters or {})
		for field, kind in (
			("device_id", "device_id"),
			("scope_id", "scope_id"),
			("user_id", "user_id"),
			("job_id", "job_id"),
		):
			if field in filters and filters[field] is not None:
				filters[field] = self.canonicalize_external_id(
					kind, filters[field])
		return filters

	def _allocate_admission_request_id(self):
		request_id = self.admission_request_id_next
		self.admission_request_id_next += 1
		return request_id

	def _estimated_usage(self, circuit, runtime):
		info = circuit.info
		shots = info.get("num_shots", info.get("shots", 1))
		estimated_ns = info.get(
			"estimated_ns",
			info.get("walltime_ns", info.get("estimated_device_ns", 0)))
		return {
			"reservation_id": runtime.reservation_id,
			"task_id": runtime.qtask_id,
			"class_id": info.get("class_id", 1),
			"event_time_ns": time.time_ns(),
			"estimated_ns": estimated_ns,
			"baseline_units": info.get("baseline_units", max(1, shots)),
			"credits": info.get("credits", info.get("estimated_credits", 1)),
			"rate_units": info.get(
				"rate_units", info.get("estimated_rate_units", 1)),
		}

	def _scheduler_task_desc(self, circuit, runtime):
		info = circuit.info
		payload = info.get("qasm")
		if isinstance(payload, str):
			payload = payload.encode("utf-8")
		return {
			"task_id": runtime.qtask_id,
			"owner_id": runtime.canonical_ids.get("owner_id", 0),
			"job_id": runtime.canonical_ids.get(
				"job_id",
				runtime.canonical_ids.get("allocation_id", 0)),
			"reservation_id": self.canonicalize_external_id(
				"reservation_id", runtime.reservation_id),
			"priority": info.get("priority", 0),
			"deadline_ns": info.get("deadline_ns", 0),
			"estimated_runtime_ns": info.get(
				"estimated_ns",
				info.get("walltime_ns", info.get("estimated_device_ns", 0))),
			"estimated_cost": info.get(
				"estimated_cost", info.get("credits", 0)),
			"payload": payload,
			"metadata": {
				100: info.get("num_shots", info.get("shots", 1)),
				101: info.get("num_qubits", 1),
				102: info.get("depth", 1),
				103: info.get("two_q_gate_count", 0),
			},
		}

	def _reconcile_task_hold_locked(self, runtime, circuit):
		hold = self.capacity_holds.pop(runtime.qtask_id, None)
		if hold is None:
			return
		reservation_id = hold["reservation_id"]
		usage = dict(hold["usage"])
		self._finalize_capacity_hold_locked(
			runtime.qtask_id, reservation_id, usage, runtime, circuit)
		self.usage_events_by_qtask_id.pop(runtime.qtask_id, None)

	def _finalize_capacity_hold_locked(self, qtask_id, reservation_id,
					   usage, runtime=None, circuit=None):
		actual = self._actual_usage(circuit, runtime, usage, qtask_id)
		unused = self._unused_usage(usage, actual)
		return_usage(self.admission_context, reservation_id, unused)
		record_actual(self.admission_context, reservation_id, actual)

	def _actual_usage(self, circuit, runtime, usage, qtask_id=None):
		observed_device_ns = self._observed_device_ns(circuit, usage)
		actual = {
			"task_id": qtask_id or (
				runtime.qtask_id if runtime is not None else
				usage.get("task_id", 0)),
			"observed_device_ns": observed_device_ns,
		}
		for field in ("baseline_units", "credits", "rate_units"):
			actual[f"actual_{field}"] = self._actual_capacity_value(
				circuit, usage, field)
		return actual

	def _observed_device_ns(self, circuit, usage):
		info = getattr(circuit, "info", {}) if circuit is not None else {}
		observed_device_ns = None
		for source in (info, usage):
			for key in ("actual_ns", "observed_device_ns"):
				if key in source:
					observed_device_ns = source[key]
					break
			if observed_device_ns is not None:
				break
		if observed_device_ns is None:
			observed_device_ns = 0
			if (circuit is not None and circuit.exec_time >= 0 and
					circuit.completion_time >= 0):
				observed_device_ns = int(
					(circuit.completion_time - circuit.exec_time) *
					1_000_000_000)
			elif circuit is not None and circuit.completion_time >= 0:
				observed_device_ns = usage.get("estimated_ns", 0)
		return observed_device_ns

	def _actual_capacity_value(self, circuit, usage, field):
		info = getattr(circuit, "info", {}) if circuit is not None else {}
		for key in (f"actual_{field}", f"observed_{field}"):
			if key in info:
				return info[key]
			if key in usage:
				return usage[key]
		if circuit is not None and circuit.completion_time >= 0:
			return usage.get(field, 0)
		return 0

	def _unused_usage(self, usage, actual):
		unused = dict(usage)
		for field, actual_field in (
				("estimated_ns", "observed_device_ns"),
				("baseline_units", "actual_baseline_units"),
				("credits", "actual_credits"),
				("rate_units", "actual_rate_units")):
			estimated = usage.get(field, 0) or 0
			actual_value = actual.get(actual_field, 0) or 0
			unused[field] = max(0, estimated - actual_value)
		unused["actual_ns"] = 0
		return unused

	def _selected_runtime_locked(self):
		for qtask_id in sorted(self.selected_qtask_ids):
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is not None:
				return runtime
			self.selected_qtask_ids.discard(qtask_id)
		return None

	def _can_select_scheduler_task_locked(self):
		if self.scheduler_control["paused"] or self.scheduler_control["draining"]:
			return False
		return (
			len(self.provider_inflight) <
			self.scheduler_control["dispatch_depth"])

	def _require_reservation_active(self, reservation, operation):
		if reservation.get("state") == "active":
			return
		raise QPMAdmissionValidationError(
			f"invalid reservation for {operation}: "
			f"state={reservation.get('state')}")

	def _require_reservation_not_expired(self, reservation):
		expires_at_ns = reservation.get("expires_at_ns")
		if not expires_at_ns:
			return
		now_ns = time.time_ns()
		if expires_at_ns > now_ns:
			return
		reservation_id = reservation.get("reservation_id")
		self._close_reservation(reservation_id, "expire", now_ns=now_ns)
		raise QPMAdmissionValidationError(
			f"expired reservation: reservation_id={reservation_id}")

	def _reservation_metadata_for_id_locked(self, reservation_id):
		if reservation_id is None:
			return {}
		metadata = self.reservation_metadata_by_id.get(reservation_id)
		if metadata is not None:
			return dict(metadata)
		reservation = get_reservation(self.admission_context, reservation_id)
		return self._reservation_metadata_locked(reservation)

	def _reservation_metadata_locked(self, reservation):
		metadata = {}
		for source in (
				reservation.get("metadata"),
				reservation.get("request_metadata"),
				self.reservation_metadata_by_id.get(
					reservation.get("reservation_id"))):
			if isinstance(source, dict):
				metadata.update(source)
		return metadata

	def _close_reservation(self, reservation_id, close_kind, reason_code=0,
			       now_ns=None):
		now_ns = now_ns or time.time_ns()
		close_state = self.reservation_close_state.setdefault(
			reservation_id,
			{
				"reason": close_kind,
				"started_at_ns": now_ns,
				"pending_removed": [],
				"held_reconciled": [],
				"scheduler_cancelled": [],
				"provider_cancelled": [],
				"provider_cancel_pending": [],
				"provider_cancel_resolved": [],
			})
		close_state.setdefault("provider_cancel_resolved", [])
		close_state["reason"] = close_kind
		self._remove_pending_for_reservation(reservation_id, close_state)
		self._reconcile_holds_for_reservation(reservation_id, close_state)
		self._refresh_provider_cancel_pending(reservation_id, close_state)
		if close_state["provider_cancel_pending"]:
			close_state["status"] = "provider-cancel-pending"
			return {
				"status": "pending",
				"reservation_id": reservation_id,
				"reason": "provider-cancel-pending",
				"pending_qtask_ids": list(
					close_state["provider_cancel_pending"]),
			}
		if close_kind == "release":
			result = release_reservation(
				self.admission_context, reservation_id, reason_code)
		elif close_kind == "cancel":
			result = cancel_reservation(
				self.admission_context, reservation_id, reason_code)
		elif close_kind == "expire":
			expire_reservations(self.admission_context, now_ns)
			result = {
				"status": "accepted",
				"reservation_id": reservation_id,
				"reason": "expired",
			}
		else:
			raise QPMAdmissionValidationError(
				f"unsupported reservation close kind: {close_kind}")
		close_state["completed_at_ns"] = time.time_ns()
		close_state["status"] = result.get("status", "accepted")
		return result

	def _remove_pending_for_reservation(self, reservation_id, close_state):
		for qtask_id, pending in list(self.pending_capacity.items()):
			if pending["reservation_id"] != reservation_id:
				continue
			self.pending_capacity.pop(qtask_id, None)
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is not None:
				self._cancel_runtime_for_reservation_close(
					runtime, close_state)
			close_state["pending_removed"].append(qtask_id)

	def _reconcile_holds_for_reservation(self, reservation_id, close_state):
		for qtask_id, hold in list(self.capacity_holds.items()):
			if hold["reservation_id"] != reservation_id:
				continue
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if (runtime is not None and
					not self._cancel_runtime_for_reservation_close(
						runtime, close_state)):
				continue
			self.capacity_holds.pop(qtask_id, None)
			usage = dict(hold["usage"])
			circuit = (
				self.circuits.get(runtime.cid)
				if runtime is not None else None)
			self._finalize_capacity_hold_locked(
				qtask_id, reservation_id, usage, runtime, circuit)
			self.usage_events_by_qtask_id.pop(qtask_id, None)
			close_state["held_reconciled"].append(qtask_id)

	def _cancel_runtime_for_reservation_close(self, runtime, close_state):
		qtask_id = runtime.qtask_id
		if runtime.scheduler_task_id is not None:
			mark_cancelled = globals().get("mark_scheduler_task_cancelled")
			scheduler_context = getattr(self, "scheduler_context", None)
			if mark_cancelled is None or scheduler_context is None:
				self._record_reservation_close_fault_locked({
					"qtask_id": qtask_id,
					"reservation_id": runtime.reservation_id,
					"reason": "scheduler-cancel-unavailable",
					"scheduler_task_id": runtime.scheduler_task_id,
				})
			else:
				self._cancel_scheduler_for_reservation_close(
					runtime, close_state, mark_cancelled,
					scheduler_context)
		selected_qtask_ids = getattr(self, "selected_qtask_ids", None)
		if selected_qtask_ids is not None:
			selected_qtask_ids.discard(qtask_id)
		provider_inflight = getattr(self, "provider_inflight", None)
		provider_active = (
			runtime.provider_handle is not None or
			(provider_inflight is not None and qtask_id in provider_inflight))
		if provider_active:
			if self._cancel_provider_for_reservation_close(
					runtime, close_state):
				if provider_inflight is not None:
					provider_inflight.discard(qtask_id)
				runtime.state = QPM_TASK_CANCELLED
				return True
			if qtask_id in close_state["provider_cancel_pending"]:
				return False
			fault = {
				"qtask_id": qtask_id,
				"reservation_id": runtime.reservation_id,
				"reason": "provider-cancel-required",
				"lifecycle_state": runtime.state,
			}
			if runtime.provider_handle is not None:
				fault["provider_handle"] = runtime.provider_handle
			self._record_reservation_close_fault_locked(fault)
			self._append_close_state_item(
				close_state, "provider_cancel_pending", qtask_id)
			return False
		if provider_inflight is not None:
			provider_inflight.discard(qtask_id)
		self.circuits.pop(runtime.cid, None)
		runtime.state = QPM_TASK_CANCELLED
		return True

	def _cancel_scheduler_for_reservation_close(self, runtime, close_state,
						    mark_cancelled,
						    scheduler_context):
		try:
			mark_cancelled(scheduler_context, runtime.scheduler_task_id)
			close_state["scheduler_cancelled"].append(runtime.qtask_id)
		except Exception as error:
			self._record_reservation_close_fault_locked({
				"qtask_id": runtime.qtask_id,
				"reservation_id": runtime.reservation_id,
				"reason": "scheduler-cancel-failed",
				"scheduler_task_id": runtime.scheduler_task_id,
				"scheduler_error": str(error),
			})

	def _record_reservation_close_fault_locked(self, fault):
		record_fault = getattr(
			self, "_record_reconciliation_fault_locked", None)
		if record_fault is not None:
			return record_fault(fault)
		record = dict(fault)
		record.setdefault("event", "reservation-close-fault")
		record.setdefault("target_id", self.config.target_id)
		record.setdefault("timestamp_ns", time.time_ns())
		self.audit_records.append(record)
		return record

	def _cancel_provider_for_reservation_close(self, runtime, close_state):
		if runtime.provider_handle is None or self.provider_canceller is None:
			return False
		try:
			status = self.provider_canceller(runtime.provider_handle)
		except Exception as error:
			self._record_reservation_close_fault_locked({
				"qtask_id": runtime.qtask_id,
				"reservation_id": runtime.reservation_id,
				"reason": "provider-cancel-failed",
				"provider_handle": runtime.provider_handle,
				"provider_error": str(error),
			})
			self._append_close_state_item(
				close_state, "provider_cancel_pending",
				runtime.qtask_id)
			return False
		if not _provider_cancel_is_terminal(status):
			self._append_close_state_item(
				close_state, "provider_cancel_pending",
				runtime.qtask_id)
			return False
		close_state["provider_cancelled"].append({
			"qtask_id": runtime.qtask_id,
			"provider_handle": runtime.provider_handle,
			"status": status,
		})
		return True

	def _cancel_provider_for_task_locked(self, runtime):
		provider_active = (
			runtime.provider_handle is not None or
			runtime.qtask_id in self.provider_inflight)
		if not provider_active:
			return None
		if runtime.provider_handle is None:
			self._record_reconciliation_fault_locked({
				"qtask_id": runtime.qtask_id,
				"reservation_id": runtime.reservation_id,
				"reason": "provider-handle-missing",
				"lifecycle_state": runtime.state,
			})
			return {
				"terminal": False,
				"status": "unsupported",
				"reason": "provider-cancel-pending",
				"message": "provider work has no cancellable handle",
			}
		if self.provider_canceller is None:
			self._record_reconciliation_fault_locked({
				"qtask_id": runtime.qtask_id,
				"reservation_id": runtime.reservation_id,
				"reason": "provider-cancel-unsupported",
				"provider_handle": runtime.provider_handle,
				"lifecycle_state": runtime.state,
			})
			return {
				"terminal": False,
				"status": "unsupported",
				"reason": "provider-cancel-pending",
				"message": "provider cancellation is unsupported",
			}
		try:
			status = self.provider_canceller(runtime.provider_handle)
		except Exception as error:
			self._record_reconciliation_fault_locked({
				"qtask_id": runtime.qtask_id,
				"reservation_id": runtime.reservation_id,
				"reason": "provider-cancel-failed",
				"provider_handle": runtime.provider_handle,
				"provider_error": str(error),
			})
			return {
				"terminal": False,
				"status": "failed",
				"reason": "provider-cancel-pending",
				"message": str(error),
			}
		if not _provider_cancel_is_terminal(status):
			return {
				"terminal": False,
				"status": status,
				"reason": "provider-cancel-pending",
			}
		return {
			"terminal": True,
			"status": status,
			"reason": "provider-cancelled",
		}

	def _append_close_state_item(self, close_state, key, value):
		if value not in close_state[key]:
			close_state[key].append(value)

	def _refresh_provider_cancel_pending(self, reservation_id, close_state):
		pending = []
		for qtask_id in close_state["provider_cancel_pending"]:
			if not self._provider_cancel_pending_resolved(qtask_id):
				pending.append(qtask_id)
				continue
			self._append_close_state_item(
				close_state, "provider_cancel_resolved", qtask_id)
		close_state["provider_cancel_pending"] = pending

	def _provider_cancel_pending_resolved(self, qtask_id):
		if qtask_id in self.capacity_holds:
			return False
		if qtask_id in self.provider_inflight:
			return False
		runtime = self.runtime_by_qtask_id.get(qtask_id)
		if runtime is None:
			runtime = self.terminal_tasks_by_qtask_id.get(qtask_id)
		if runtime is None:
			return True
		return runtime.state in QPM_TASK_TERMINAL_STATES

_CONTROLLERS = {}
_CONTROLLERS_LOCK = threading.RLock()


def controller_config(qrc, target_id=None, admission_threading_mode=None,
		      scheduler_threading_mode=None, serialization_mode=None):
	return QPMControllerConfig(
		target_id=_target_id(qrc, target_id),
		admission_threading_mode=(
			admission_threading_mode or
			os.environ.get(ADMISSION_THREADING_ENV) or
			DEFAULT_ADMISSION_THREADING_MODE),
		scheduler_threading_mode=(
			scheduler_threading_mode or
			os.environ.get(SCHEDULER_THREADING_ENV) or
			DEFAULT_SCHEDULER_THREADING_MODE),
		serialization_mode=(
			serialization_mode or
			os.environ.get(CONTROLLER_SERIALIZATION_ENV) or
			DEFAULT_CONTROLLER_SERIALIZATION_MODE),
	)


def get_target_controller(config, max_ppn, admission_context_factory=None,
			  scheduler_context_factory=None):
	with _CONTROLLERS_LOCK:
		controller = _CONTROLLERS.get(config.target_id)
		if controller is None:
			controller = QPMTargetController(
				config,
				admission_context_factory=admission_context_factory,
				scheduler_context_factory=scheduler_context_factory)
			_CONTROLLERS[config.target_id] = controller
		return controller.bind(max_ppn)


def clear_target_controllers():
	with _CONTROLLERS_LOCK:
		_CONTROLLERS.clear()


def _provider_cancel_is_terminal(status):
	if status is True:
		return True
	if status in (None, False):
		return False
	if isinstance(status, dict):
		status = status.get("status") or status.get("outcome")
		if status is None:
			return False
	value = str(status).strip().lower()
	return value not in {
		"",
		"pending",
		"unsupported",
		"not-supported",
		"deferred",
	}


def _target_id(qrc, explicit_target_id):
	if explicit_target_id:
		return str(explicit_target_id)

	for env_name in (TARGET_ID_ENV, DEVICE_ID_ENV):
		value = os.environ.get(env_name)
		if value:
			return value

	qrc_type = type(qrc)
	return f"{qrc_type.__module__}.{qrc_type.__name__}"


def _create_admission_context(config, admission_context_factory):
	if config.admission_threading_mode == QHW_ADM_THREAD_USER:
		if config.serialization_mode != DEFAULT_CONTROLLER_SERIALIZATION_MODE:
			raise ValueError(
				"QHW_ADM_THREAD_USER requires controller-lock "
				"serialization")
	if admission_context_factory is None:
		admission_context_factory = create_admission_context
	return admission_context_factory(config.admission_threading_mode)


def _create_scheduler_context(config, scheduler_context_factory):
	if config.scheduler_threading_mode == QHW_SCHED_THREAD_USER:
		if config.serialization_mode != DEFAULT_CONTROLLER_SERIALIZATION_MODE:
			raise ValueError(
				"QHW_SCHED_THREAD_USER requires controller-lock "
				"serialization")
	if scheduler_context_factory is None:
		scheduler_context_factory = create_scheduler_context
	return scheduler_context_factory(
		config.scheduler_threading_mode,
		target_id=config.target_id)


def _owner_identifier(owner):
	for key in ("user_id", "user", "username", "account"):
		value = owner.get(key)
		if value is not None:
			return value
	return None


def _event_filter_value(payload, runtime, key):
	if isinstance(payload, dict) and key in payload:
		return payload[key]
	if runtime is None:
		return None
	if key == "cid":
		return runtime.cid
	if key == "qtask_id":
		return runtime.qtask_id
	if key == "reservation_id":
		return runtime.reservation_id
	return None


def _filter_value_matches(expected, actual):
	if isinstance(expected, (list, tuple, set)):
		return actual in expected
	return expected == actual


def _numeric_or_canonical(controller, kind, numeric_value, external_value):
	if numeric_value is not None:
		return int(numeric_value)
	if isinstance(external_value, int) and not isinstance(external_value, bool):
		return external_value
	return controller.canonicalize_external_id(kind, external_value)


def _token_metadata(token):
	if token is None:
		return {}
	return {
		"present": True,
		"type": type(token).__name__,
	}
