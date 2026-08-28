import os
import threading
import time
from collections import deque
from copy import deepcopy
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
	estimate_qtask_class as estimate_admission_qtask_class,
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
	QPMSchedulerError,
	QPMSchedulerQueueEmpty,
)
from .credentials import (
	bind_reservation_credential,
	validate_reservation_credential,
)


TARGET_ID_ENV = "QFW_QPM_TARGET_ID"
DEVICE_ID_ENV = "QFW_QPU_DEVICE_ID"
ADMISSION_THREADING_ENV = "QFW_QPM_ADMISSION_THREADING_MODE"
SCHEDULER_THREADING_ENV = "QFW_QPM_SCHEDULER_THREADING_MODE"
CONTROLLER_SERIALIZATION_ENV = "QFW_QPM_CONTROLLER_SERIALIZATION_MODE"
SITE_CONFIG_ENV = "QFW_SITE_CONFIG"
COMPLETION_RETENTION_DEFAULTS = {
	"completion_ttl_seconds": 3600,
	"terminal_reservation_retention_seconds": 3600,
	"max_records_per_reservation": 1024,
	"max_bytes_per_reservation": 67108864,
	"purge_interval_seconds": 60,
}
COMPLETION_RETENTION_CONFIG_KEYS = {
	"completion-ttl-seconds": "completion_ttl_seconds",
	"terminal-reservation-retention-seconds": (
		"terminal_reservation_retention_seconds"),
	"max-records-per-reservation": "max_records_per_reservation",
	"max-bytes-per-reservation": "max_bytes_per_reservation",
	"purge-interval-seconds": "purge_interval_seconds",
}
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

RESERVATION_BINDING_SCHEMA = "qfw-reservation-binding-v1"
SENSITIVE_METADATA_KEY_PARTS = (
	"api_key",
	"apikey",
	"authorization",
	"bearer",
	"client_secret",
	"password",
	"private_key",
	"secret",
	"token",
)
DEFAULT_RESERVATION_OPERATIONS = (
	"execution",
	"status",
	"result",
	"cancel",
)

TELEMETRY_METHOD_LABELS = {
	"get_backend_info": TELEMETRY_BASIC_DISCOVERY,
	"get_device_info": TELEMETRY_BASIC_DISCOVERY,
	"get_dynamic_backend_info": TELEMETRY_BASIC_DISCOVERY,
	"get_calibration_snapshot": TELEMETRY_BASIC_DISCOVERY,
	"get_coupling_graph": TELEMETRY_BASIC_DISCOVERY,
	"get_task_timing": TELEMETRY_CALLER_OWNED,
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
	credential_mode: str = "no-secret"
	admission_threading_mode: str = DEFAULT_ADMISSION_THREADING_MODE
	scheduler_threading_mode: str = DEFAULT_SCHEDULER_THREADING_MODE
	serialization_mode: str = DEFAULT_CONTROLLER_SERIALIZATION_MODE

	def telemetry(self):
		return {
			"target_id": self.target_id,
			"credential_mode": self.credential_mode,
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


@dataclass
class QPMCompletionQueue:
	reservation_id: object
	records: deque = field(default_factory=deque)
	records_by_cid: dict = field(default_factory=dict)
	records_by_qtask_id: dict = field(default_factory=dict)
	dequeued_records: list = field(default_factory=list)
	evicted_selectors: dict = field(default_factory=dict)
	retained_bytes: int = 0
	terminal_at_ns: int = None
	last_purge_ns: int = 0


class _CompletionEvent:
	def __init__(self, evtype, payload):
		self.evtype = evtype
		self.payload = payload

	def get_evtype(self):
		return self.evtype

	def get_event(self):
		return self.payload


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
		self.provider_credential_evictor = None
		self.timeout_state = {}
		self.result_state = {}
		self.completion_retention = completion_retention_config()
		self.completion_queues = {}
		self.completion_evictions_by_reservation = {}
		self.completion_last_purge_ns = 0
		self.completion_purge_stop = threading.Event()
		self.completion_purge_thread = None
		self.terminal_tasks_by_cid = {}
		self.terminal_tasks_by_qtask_id = {}
		self.audit_records = []
		self.lifecycle_events = []
		self.service_state = "running"
		self.shutdown_request = None
		self.reconciliation_faults = []
		self.external_id_maps = {}
		self.external_id_next = 1
		self.admission_request_id_next = 1
		self.reservation_metadata_by_id = {}
		self.reservation_credentials_by_id = {}
		self.credential_cleanup_queue = []
		self.reservation_close_state = {}
		self.device_profile = None
		self.admission_configuration = {}
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
			"admission_policy": 0,
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

	def set_provider_credential_evictor(self, evictor):
		with self.lock:
			self.provider_credential_evictor = evictor

	def telemetry(self):
		info = self.config.telemetry()
		with self.lock:
			info.update({
				"binding_count": self.binding_count,
				"max_ppn": self.max_ppn,
				"resource_hosts": sorted(self.free_hosts.keys()),
				"runtime_task_count": len(self.runtime_by_qtask_id),
				"audit_record_count": len(self.audit_records),
				"lifecycle_event_count": len(self.lifecycle_events),
				"reconciliation_fault_count": len(
					self.reconciliation_faults),
				"completion_queue_count": len(self.completion_queues),
				"completion_retention": dict(self.completion_retention),
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
				"credential_binding_count": len(
					self.reservation_credentials_by_id),
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
				"dispatch_limits": self._dispatch_limits_locked(),
			}

	def get_service_status(self, initialized=False, provider_ready=False):
		with self.lock:
			return {
				"state": self.service_state,
				"initialized": bool(initialized),
				"ready": bool(initialized and provider_ready and
					self.service_state == "running"),
				"accepting_requests": self.service_state == "running",
				"provider_ready": bool(provider_ready),
				"active_task_count": sum(
					1 for runtime in self.runtime_by_qtask_id.values()
					if runtime.state not in QPM_TASK_TERMINAL_STATES),
				"active_reservation_count": len(
					self.reservation_metadata_by_id),
				"shutdown": deepcopy(self.shutdown_request),
			}

	def begin_service_shutdown(self, mode, timeout_s, reason, token=None):
		with self.lock:
			if self.shutdown_request is not None:
				return dict(self.shutdown_request, repeated=True)
			self.service_state = (
				"draining" if mode == "graceful" else "quiescing")
			self.scheduler_control["paused"] = True
			self.scheduler_control["draining"] = mode == "graceful"
			request = {
				"status": "accepted",
				"mode": mode,
				"timeout_s": timeout_s,
				"reason": reason,
				"state": self.service_state,
				"requested_at_ns": time.time_ns(),
			}
			self.shutdown_request = dict(request)
			self.audit_records.append({
				"operation": "shutdown",
				"reason": reason,
				"mode": mode,
				"token_metadata": _token_metadata(token),
				"timestamp": time.time(),
			})
			self._record_lifecycle_event_locked(
				"service-shutdown-requested", reason=reason,
				details={"mode": mode, "timeout_s": timeout_s})
			return request

	def record_control_reconciliation(self, reason, summary, token=None):
		with self.lock:
			self.audit_records.append({
				"operation": "reconcile_runtime_state",
				"reason": reason,
				"summary": deepcopy(summary),
				"token_metadata": _token_metadata(token),
				"timestamp": time.time(),
			})
			return summary

	def set_service_state(self, state):
		with self.lock:
			self.service_state = state
			self._record_lifecycle_event_locked(
				"service-state-change", details={"state": state})

	def get_scheduler_policy(self):
		with self.lock:
			return {
				"version": (
					self.scheduler_config_versions["scheduler_policy"]),
				"scheduler_policy": dict(self.scheduler_policy),
			}

	def configure_scheduler_policy(self, configuration):
		with self.lock:
			normalized = activate_scheduler_policy(
				self.scheduler_context, configuration)
			if self.scheduler_policy == normalized:
				return {
					"status": "unchanged",
					"version": self.scheduler_config_versions[
						"scheduler_policy"],
					"scheduler_policy": dict(self.scheduler_policy),
				}
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

	def configure_dispatch_limits(self, limits):
		with self.lock:
			limits = dict(limits or {})
			unknown = set(limits) - {"max_inflight"}
			if unknown:
				raise ValueError(
					"unsupported dispatch limits: " +
					", ".join(sorted(unknown)))
			if "max_inflight" not in limits:
				raise ValueError("dispatch limits require max_inflight")
			depth = int(limits["max_inflight"])
			if depth < 0:
				raise ValueError("max_inflight must not be negative")
			if self.scheduler_control["dispatch_depth"] == depth:
				return self._scheduler_control_result(
					"unchanged",
					self.scheduler_config_versions["scheduler_control"])
			self.scheduler_control["dispatch_depth"] = depth
			version = self._bump_scheduler_config_version(
				"scheduler_control")
			self._record_lifecycle_event_locked(
				"scheduler-dispatch-limits-change",
				details={
					"version": version,
					"max_inflight": depth,
				})
			return self._scheduler_control_result(
				"dispatch-limits-updated", version)

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
				"dispatch_limits": self._dispatch_limits_locked(),
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
				"scheduler_queue_depth": (
					self._active_scheduler_task_count_locked()),
				"scheduler_task_count": scheduler_task_count(
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
				"scheduler_depth": (
					self._active_scheduler_task_count_locked()),
				"scheduler_task_count": scheduler_task_count(
					self.scheduler_context),
				"active_task_count": len(self.runtime_by_qtask_id),
				"selected_task_count": len(self.selected_qtask_ids),
				"provider_inflight_count": len(self.provider_inflight),
				"dispatch_limits": self._dispatch_limits_locked(),
				"held_capacity": self._held_capacity_locked(),
				"in_flight_capacity": self._in_flight_capacity_locked(),
				"policy_state": {
					"scheduler": dict(self.scheduler_policy),
					"admission": deepcopy(
						self.admission_configuration),
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
				"reconciliation_faults": [
					dict(record)
					for record in self.reconciliation_faults
				],
				"telemetry": self._telemetry_object_label(
					"service-lifecycle", access_class),
			}

	def record_defw_directory_event(self, event_type, service_record=None,
					peer_event=None, reason=None, details=None):
		with self.lock:
			records = self._defw_directory_event_records_locked(
				event_type, service_record=service_record,
				peer_event=peer_event, reason=reason,
				details=details)
			for record in records:
				self.lifecycle_events.append(record)
				self.audit_records.append(record)
			return [dict(record) for record in records]

	def cancel_task(self, cid=None, qtask_id=None, reason=None,
			reservation_id=None, require_reservation=False):
		deliveries = []
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
			self._append_terminal_completion_delivery_locked(
				deliveries, runtime, response)
		self._dispatch_completion_deliveries(deliveries)
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
		return self.publish_completion_event(event)

	def publish_completion_event(self, event):
		payload = event.get_event()
		evtype = event.get_evtype()
		return self.publish_completion(payload, evtype=evtype, event=event)

	def publish_completion(self, completion, evtype=None, event=None):
		payload = completion if isinstance(completion, dict) else {
			"result": completion,
		}
		now_ns = time.time_ns()
		delivery = None
		with self.lock:
			self._purge_completion_queues_locked(now_ns)
			runtime = self._completion_runtime_locked(payload)
			if runtime is None or runtime.reservation_id is None:
				return False
			payload_failed = (
				_completion_payload_failed(payload) or
				runtime.state == QPM_TASK_FAILED)
			if payload_failed:
				payload.setdefault("outcome", "FAILED")
				payload.setdefault("reason", "provider-execution-failed")
			circuit = self.circuits.get(runtime.cid)
			if (runtime.state not in QPM_TASK_TERMINAL_STATES and
					circuit is not None):
				if payload_failed:
					self._fail_scheduled_task_locked(
						circuit,
						error=payload.get("error"),
						reason=payload.get("reason"))
				else:
					self.complete_scheduled_task(circuit, result=payload)
			record = self._completion_record_locked(
				payload, runtime, now_ns)
			queue = self._ensure_completion_queue_locked(
				runtime.reservation_id)
			self._enqueue_completion_record_locked(queue, record)
			delivery = self._completion_delivery_locked(
				evtype, record, event=event)
		self._dispatch_completion_deliveries([delivery])
		return True

	def peek_completion(self, reservation_id=None, cid=None, qtask_id=None,
			    operation="peek_cq"):
		with self.lock:
			self._purge_completion_queues_locked(time.time_ns())
			return self._poll_completion_locked(
				reservation_id, cid=cid, qtask_id=qtask_id,
				consume=False, operation=operation)

	def read_completion(self, reservation_id=None, cid=None, qtask_id=None,
			    operation="read_cq"):
		with self.lock:
			self._purge_completion_queues_locked(time.time_ns())
			return self._poll_completion_locked(
				reservation_id, cid=cid, qtask_id=qtask_id,
				consume=True, operation=operation)

	def purge_completion_queues(self, now_ns=None):
		with self.lock:
			return self._purge_completion_queues_locked(
				now_ns or time.time_ns(), force=True)

	def start_completion_purge_worker(self):
		with self.lock:
			thread = self.completion_purge_thread
			if thread is not None and thread.is_alive():
				return {"status": "running"}
			self.completion_purge_stop.clear()
			thread = threading.Thread(
				target=self._completion_purge_worker,
				name=f"qpm-completion-purge-{self.config.target_id}")
			thread.daemon = True
			self.completion_purge_thread = thread
			thread.start()
			return {
				"status": "started",
				"purge_interval_seconds": self.completion_retention[
					"purge_interval_seconds"],
			}

	def stop_completion_purge_worker(self, timeout=1):
		with self.lock:
			thread = self.completion_purge_thread
			if thread is None:
				return {"status": "stopped"}
			self.completion_purge_stop.set()
		if thread is not threading.current_thread():
			thread.join(timeout=timeout)
		with self.lock:
			status = "running" if thread.is_alive() else "stopped"
			if status == "stopped":
				self.completion_purge_thread = None
			return {"status": status}

	def _completion_purge_worker(self):
		while True:
			interval_seconds = self.completion_retention[
				"purge_interval_seconds"]
			if self.completion_purge_stop.wait(interval_seconds):
				break
			try:
				self.purge_completion_queues(now_ns=time.time_ns())
			except Exception:
				pass

	def evaluate_reservation(self, request, token=None):
		with self.lock:
			admission_request = self._admission_request(request, token=token)
			return evaluate_request(self.admission_context, admission_request)

	def reserve_admission(self, request, token=None):
		with self.lock:
			if self.service_state != "running":
				return {
					"status": "rejected",
					"reason": "service-quiescing",
					"state": self.service_state,
				}
			admission_request = self._admission_request(request, token=token)
			try:
				validate_reservation_credential(
					admission_request["metadata"]["reservation_binding"],
					credential_mode=self.config.credential_mode)
			except Exception as error:
				return {
					"status": "rejected",
					"request_id": admission_request.get("request_id"),
					"reason": "credential-eligibility-failed",
					"message": str(error),
				}
			decision = reserve_request(self.admission_context, admission_request)
			reservation_id = decision.get("reservation_id")
			if (decision.get("status") == "accepted" and
					reservation_id is not None):
				metadata = admission_request["metadata"]
				try:
					self._bind_provider_credential_locked(
						reservation_id, metadata)
				except Exception as error:
					self._reject_credential_binding_reservation_locked(
						reservation_id)
					return {
						"status": "rejected",
						"request_id": decision.get("request_id"),
						"reservation_id": reservation_id,
						"reason": "credential-binding-failed",
						"message": str(error),
						"admission_status": dict(decision),
					}
				self.reservation_metadata_by_id[reservation_id] = metadata
				self._ensure_completion_queue_locked(reservation_id)
			return decision

	def renew_admission(self, reservation_id, request=None, token=None):
		with self.lock:
			result = renew_reservation(
				self.admission_context, reservation_id, request or {})
			return result

	def release_admission(self, reservation_id, reason_code=0, token=None):
		deliveries = []
		with self.lock:
			result = self._close_reservation(
				reservation_id, "release", reason_code=reason_code,
				deliveries=deliveries)
			if result.get("status") == "accepted":
				self.reservation_metadata_by_id.pop(reservation_id, None)
				self._remove_provider_credential_locked(reservation_id)
		self._dispatch_completion_deliveries(deliveries)
		cleanup_errors = self._drain_provider_credential_cleanup()
		if cleanup_errors:
			result["credential_cleanup_errors"] = cleanup_errors
		return result

	def cancel_admission(self, reservation_id, reason_code=0, token=None):
		deliveries = []
		with self.lock:
			result = self._close_reservation(
				reservation_id, "cancel", reason_code=reason_code,
				deliveries=deliveries)
			if result.get("status") == "accepted":
				self.reservation_metadata_by_id.pop(reservation_id, None)
				self._remove_provider_credential_locked(reservation_id)
		self._dispatch_completion_deliveries(deliveries)
		cleanup_errors = self._drain_provider_credential_cleanup()
		if cleanup_errors:
			result["credential_cleanup_errors"] = cleanup_errors
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
		try:
			with self.lock:
				reservation = get_reservation(
					self.admission_context, reservation_id)
				if reservation_id in self.reservation_close_state:
					raise QPMAdmissionValidationError(
						f"reservation is closing: "
						f"reservation_id={reservation_id}")
				self._require_reservation_active(reservation, operation)
				self._require_reservation_not_expired(reservation)
				self._require_reservation_matches_context_locked(
					reservation, request_context)
				return reservation
		finally:
			self._drain_provider_credential_cleanup()

	def provider_credential_for_reservation(self, reservation_id,
						operation="execution"):
		try:
			with self.lock:
				if reservation_id is None:
					raise QPMAdmissionValidationError(
						"reservation_id is required for provider credentials")
				reservation = get_reservation(
					self.admission_context, reservation_id)
				self._require_reservation_active(reservation, operation)
				self._require_reservation_not_expired(reservation)
				record = self.reservation_credentials_by_id.get(reservation_id)
				if record is None:
					record = self._fallback_provider_credential_locked(
						reservation_id)
				expires_at_ns = record["metadata"].get("expires_at_ns")
				if expires_at_ns and expires_at_ns <= time.time_ns():
					raise QPMAdmissionValidationError(
						"provider credential binding expired for "
						f"reservation_id={reservation_id}")
				return {
					"secret": dict(record.get("secret") or {}),
					"metadata": dict(record.get("metadata") or {}),
				}
		finally:
			self._drain_provider_credential_cleanup()

	def attach_provider_credential(self, circuit):
		reservation_id = circuit.info.get("reservation_id")
		record = self.provider_credential_for_reservation(
			reservation_id, operation="execution")
		secret = dict(record.get("secret") or {})
		secret["reservation_id"] = reservation_id
		metadata = _drop_none(_redacted_metadata(
			dict(record.get("metadata") or {})))
		setattr(circuit, "provider_credential", secret)
		setattr(circuit, "provider_credential_metadata", metadata)
		if metadata:
			circuit.info["provider_credential_binding"] = metadata
		return circuit

	def clear_provider_credentials(self):
		with self.lock:
			for reservation_id in list(self.reservation_credentials_by_id):
				self._remove_provider_credential_locked(reservation_id)
		return self._drain_provider_credential_cleanup()

	def _bind_provider_credential_locked(self, reservation_id, metadata):
		reservation_binding = dict(metadata.get("reservation_binding") or {})
		provider, response = bind_reservation_credential(
			reservation_binding,
			credential_mode=self.config.credential_mode)
		secret = dict(response.secret or {})
		credential_metadata = _drop_none(_redacted_metadata(
			dict(response.metadata or {})))
		self.reservation_credentials_by_id[reservation_id] = {
			"secret": secret,
			"metadata": credential_metadata,
			"binding": reservation_binding,
			"provider": provider,
		}
		if credential_metadata:
			metadata["provider_credential_binding"] = credential_metadata
		return credential_metadata

	def _remove_provider_credential_locked(self, reservation_id):
		record = self.reservation_credentials_by_id.pop(reservation_id, None)
		if record is not None or self.provider_credential_evictor is not None:
			self.credential_cleanup_queue.append((reservation_id, record))
		return record

	def _drain_provider_credential_cleanup(self):
		with self.lock:
			queued = self.credential_cleanup_queue
			self.credential_cleanup_queue = []
		errors = []
		for reservation_id, record in queued:
			provider = record.pop("provider", None) if record else None
			try:
				if provider is not None:
					provider.release(record)
			except Exception as error:
				errors.append({
					"reservation_id": reservation_id,
					"stage": "credential-provider-release",
					"error": str(error),
				})
			try:
				if self.provider_credential_evictor is not None:
					self.provider_credential_evictor(reservation_id)
			except Exception as error:
				errors.append({
					"reservation_id": reservation_id,
					"stage": "provider-client-eviction",
					"error": str(error),
				})
		if errors:
			with self.lock:
				for error in errors:
					self._record_reconciliation_fault_locked({
						"reason": "credential-cleanup-failed",
						**error,
					})
		return errors

	def _fallback_provider_credential_locked(self, reservation_id):
		request_metadata = self.reservation_metadata_by_id.get(reservation_id)
		if isinstance(request_metadata, dict):
			credential_metadata = (
				request_metadata.get("provider_credential_binding") or {})
			if credential_metadata.get("secret_material") == "cached-in-qpm":
				raise QPMAdmissionValidationError(
					"provider credential binding is missing for "
					f"reservation_id={reservation_id}")
		now_ns = time.time_ns()
		record = {
			"secret": {},
			"metadata": {
				"schema": "qfw-provider-credential-binding-v1",
				"provider": "no-secret",
				"provider_type": "no-secret",
				"reservation_id": reservation_id,
				"bound_at_ns": now_ns,
				"expires_at_ns": 0,
				"refresh_policy": "none",
				"secret_material": "none",
			},
			"binding": {},
		}
		self.reservation_credentials_by_id[reservation_id] = record
		return record

	def _reject_credential_binding_reservation_locked(self, reservation_id):
		self.reservation_metadata_by_id.pop(reservation_id, None)
		self.reservation_credentials_by_id.pop(reservation_id, None)
		self.completion_queues.pop(reservation_id, None)
		try:
			release_reservation(self.admission_context, reservation_id, 1)
		except Exception:
			try:
				cancel_reservation(self.admission_context, reservation_id, 1)
			except Exception:
				pass

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
		deliveries = []
		scheduler_error = None
		scheduler_cause = None
		with self.lock:
			runtime = self.runtime_by_qtask_id[qtask_id]
			if runtime.scheduler_task_id is not None:
				return runtime
			if qtask_id not in self.capacity_holds:
				raise QPMAdmissionValidationError(
					"scheduler submission requires an active "
					"admission capacity hold")
			try:
				scheduler_task_id = submit_scheduler_task(
					self.scheduler_context,
					self._scheduler_task_desc(circuit, runtime))
			except Exception as error:
				self._fail_scheduled_task_locked(
					circuit, error=error,
					reason="scheduler-submission-failed",
					deliveries=deliveries)
				scheduler_error = QPMSchedulerError(
					"scheduler task submission failed: "
					f"qtask_id={qtask_id} error={error}")
				scheduler_cause = error
			if scheduler_error is not None:
				runtime_result = None
			else:
				runtime.scheduler_task_id = scheduler_task_id
				self.qtask_id_by_scheduler_task_id[scheduler_task_id] = qtask_id
				runtime.state = QPM_TASK_QUEUED
				runtime_result = runtime
		self._dispatch_completion_deliveries(deliveries)
		if scheduler_error is not None:
			raise scheduler_error from scheduler_cause
		return runtime_result

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
			if (qtask_id in self.provider_inflight or
					runtime.state == QPM_TASK_SUBMITTED):
				return None
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
		self._drain_provider_credential_cleanup()
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

	def configure_device_profile(self, profile=None):
		with self.lock:
			normalized = self._normalize_device_profile(profile or {})
			if self.device_profile == normalized:
				return {
					"status": "unchanged",
					"version": self.admission_config_versions["device_profile"],
					"device_profile": dict(self.device_profile),
				}
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

	def set_admission_policy(self, configuration, device_id=None):
		with self.lock:
			normalized, profile = self._normalize_admission_configuration(
				configuration, device_id=device_id)
			version = self.admission_config_versions["admission_policy"]
			expected = normalized.pop("expected_version", None)
			if expected is not None and expected != version:
				raise QPMAdmissionValidationError(
					"admission configuration version mismatch: "
					f"expected={expected} actual={version}")
			if self.admission_configuration == normalized:
				return {
					"status": "unchanged",
					"version": version,
					"configuration": deepcopy(normalized),
				}
			old_profile = deepcopy(self.device_profile)
			old_configuration = deepcopy(self.admission_configuration)
			profile_changed = old_profile != profile
			try:
				if profile_changed:
					register_device_profile(self.admission_context, profile)
				set_estimator(
					self.admission_context, normalized["device_id"],
					normalized["estimator"],
					policy=normalized["policy"], device_profile=profile)
				set_policy(
					self.admission_context, normalized["device_id"],
					normalized["policy"],
					estimator=normalized["estimator"],
					device_profile=profile)
			except Exception:
				self._restore_admission_configuration(
					old_configuration, old_profile)
				raise
			self.device_profile = profile
			self.admission_configuration = normalized
			if profile_changed:
				self._bump_admission_config_version("device_profile")
			version = self._bump_admission_config_version("admission_policy")
			return {
				"status": "accepted",
				"version": version,
				"configuration": deepcopy(normalized),
			}

	def get_admission_policy(self, device_id=None):
		with self.lock:
			configuration = deepcopy(self.admission_configuration)
			if (device_id is not None and configuration and
					configuration["device_id"] != device_id):
				raise QPMAdmissionValidationError(
					f"admission policy is not configured for device {device_id}")
			return {
				"version": self.admission_config_versions["admission_policy"],
				"configuration": configuration or None,
			}

	def register_circuit(self, cid, request_context, payload):
		with self.lock:
			qtask_id = self.allocate_qtask_id()
			payload["qtask_id"] = qtask_id
			reservation_metadata = self._reservation_metadata_for_id_locked(
				request_context.reservation_id)
			request_metadata = _request_metadata(
				reservation_metadata, request_context.metadata)
			owner_metadata = request_metadata.get("owner", {})
			if not isinstance(owner_metadata, dict):
				owner_metadata = {}
			runtime = QPMRuntimeTask(
				cid=cid,
				qtask_id=qtask_id,
				reservation_id=request_context.reservation_id,
				token_metadata=_token_metadata(request_context.token),
				owner_metadata=dict(owner_metadata),
				request_metadata=request_metadata,
			)
			runtime.external_ids, runtime.canonical_ids = (
				self.canonicalize_reservation_metadata(request_metadata))
			self.terminal_tasks_by_cid.pop(cid, None)
			self.runtime_by_cid[cid] = runtime
			self.runtime_by_qtask_id[qtask_id] = runtime
			if runtime.reservation_id is not None:
				self._ensure_completion_queue_locked(runtime.reservation_id)
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
		owner_id = (
			_metadata_identifier(metadata, "user_id", "external_user_id") or
			_owner_identifier(owner))
		for kind, value in (
			("owner_id", owner_id),
			("job_id", _metadata_identifier(
				metadata, "job_id", "external_job_id")),
			("allocation_id", _metadata_identifier(
				metadata, "allocation_id", "external_allocation_id")),
			("project_id", _metadata_identifier(
				metadata, "project_id", "external_project_id")),
			("session_id", _metadata_identifier(
				metadata, "session_id", "external_session_id")),
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

	def complete_scheduled_task(self, circuit, result=None):
		qtask_id = circuit.info["qtask_id"]
		with self.lock:
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is None:
				return None
			if runtime.state in QPM_TASK_TERMINAL_STATES:
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

	def fail_scheduled_task(self, circuit, error=None, reason=None,
				publish_completion=True):
		deliveries = [] if publish_completion else None
		with self.lock:
			runtime = self._fail_scheduled_task_locked(
				circuit, error=error, reason=reason,
				deliveries=deliveries)
		if deliveries is not None:
			self._dispatch_completion_deliveries(deliveries)
		return runtime

	def _fail_scheduled_task_locked(self, circuit, error=None, reason=None,
					deliveries=None):
		qtask_id = circuit.info["qtask_id"]
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
				"reason": reason,
				"error": str(error),
				"error_type": type(error).__name__,
			}
		if deliveries is not None:
			self._append_terminal_completion_delivery_locked(
				deliveries, runtime,
				self._task_status_locked(
					runtime, outcome="FAILED", reason=reason))
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
			if runtime.state != QPM_TASK_FAILED:
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

	def _ensure_completion_queue_locked(self, reservation_id):
		queue = self.completion_queues.get(reservation_id)
		if queue is None:
			queue = QPMCompletionQueue(reservation_id=reservation_id)
			self.completion_queues[reservation_id] = queue
		return queue

	def _mark_completion_queue_terminal_locked(self, reservation_id, now_ns):
		queue = self._ensure_completion_queue_locked(reservation_id)
		if queue.terminal_at_ns is None:
			queue.terminal_at_ns = now_ns

	def _completion_runtime_locked(self, payload):
		cid = payload.get("cid") if isinstance(payload, dict) else None
		qtask_id = (
			payload.get("qtask_id") if isinstance(payload, dict) else None)
		runtime = self._runtime_for_task_selector_locked(
			cid=cid, qtask_id=qtask_id)
		if runtime is None:
			runtime = self._terminal_task_for_selector_locked(
				cid=cid, qtask_id=qtask_id)
		if runtime is None and isinstance(payload, dict):
			provider_handle = payload.get("provider_handle")
			if provider_handle is not None:
				runtime = self.task_for_provider_handle(provider_handle)
		return runtime

	def _completion_record_locked(self, payload, runtime, now_ns):
		if isinstance(payload, dict):
			payload.setdefault("cid", runtime.cid)
			payload.setdefault("qtask_id", runtime.qtask_id)
			payload.setdefault("reservation_id", runtime.reservation_id)
			payload.setdefault("scheduler_task_id", runtime.scheduler_task_id)
			record = deepcopy(payload)
		else:
			record = {"result": payload}
		record["cid"] = runtime.cid
		record["qtask_id"] = runtime.qtask_id
		record["reservation_id"] = runtime.reservation_id
		if runtime.scheduler_task_id is not None:
			record["scheduler_task_id"] = runtime.scheduler_task_id
		record.setdefault("cq_enqueue_time", time.time())
		record.setdefault("cq_dequeue_time", -1)
		record["completion_ready"] = True
		record["qpm_cq_enqueue_time_ns"] = now_ns
		record["qpm_cq_dequeue_time_ns"] = -1
		record["_qpm_record_size_bytes"] = (
			_completion_record_size_bytes(record))
		return record

	def _append_terminal_completion_delivery_locked(
			self, deliveries, runtime, payload=None, evtype=None):
		delivery = self._terminal_completion_delivery_locked(
			runtime, payload=payload, evtype=evtype)
		if delivery is not None:
			deliveries.append(delivery)
		return delivery

	def _terminal_completion_delivery_locked(
			self, runtime, payload=None, evtype=None):
		if runtime is None or runtime.reservation_id is None:
			return None
		now_ns = time.time_ns()
		if payload is None:
			payload = self._task_status_locked(runtime)
		record = self._completion_record_locked(
			dict(payload), runtime, now_ns)
		queue = self._ensure_completion_queue_locked(
			runtime.reservation_id)
		self._enqueue_completion_record_locked(queue, record)
		return self._completion_delivery_locked(evtype, record)

	def _completion_delivery_locked(self, evtype, record, event=None):
		event_type = evtype or self.push_info.get("evtype") or "completion"
		matches = [
			registration
			for registrations in self.event_endpoints.values()
			for registration in registrations
			if self._event_registration_matches_locked(
				registration, event_type, record)
		]
		if event is None or evtype is None:
			event = _CompletionEvent(event_type, record)
		return {
			"event": event,
			"matches": matches,
		}

	def _dispatch_completion_deliveries(self, deliveries):
		delivered = False
		stale_registration_ids = set()
		for delivery in deliveries:
			if delivery is None:
				continue
			for registration in delivery["matches"]:
				try:
					registration["class"].put(delivery["event"])
				except Exception:
					stale_registration_ids.add(id(registration))
					continue
				delivered = True
		if stale_registration_ids:
			with self.lock:
				for class_id in list(self.event_endpoints):
					registrations = [
						registration
						for registration in self.event_endpoints[class_id]
						if id(registration) not in stale_registration_ids
					]
					if registrations:
						self.event_endpoints[class_id] = registrations
					else:
						self.event_endpoints.pop(class_id, None)
		return delivered

	def _enqueue_completion_record_locked(self, queue, record):
		queue.records.append(record)
		self._completion_index_add_locked(queue, record)
		queue.retained_bytes += record["_qpm_record_size_bytes"]
		self._enforce_completion_retention_locked(queue, time.time_ns())
		return record

	def _poll_completion_locked(self, reservation_id, cid=None,
				    qtask_id=None, consume=False,
				    operation="peek_cq"):
		queue, error = self._completion_queue_for_poll_locked(
			reservation_id, operation, cid=cid, qtask_id=qtask_id)
		if error is not None:
			return error
		record = self._completion_record_for_selector_locked(
			queue, cid=cid, qtask_id=qtask_id)
		selector_error = self._completion_selector_reservation_error_locked(
			reservation_id, cid=cid, qtask_id=qtask_id,
			record=record)
		if selector_error is not None:
			selector_error["poll_operation"] = operation
			selector_error["completion_ready"] = False
			return selector_error
		if record is None:
			eviction = self._completion_eviction_for_selector_locked(
				reservation_id, cid=cid, qtask_id=qtask_id)
			if eviction is not None:
				return self._completion_no_longer_retained_response(
					reservation_id, cid=cid, qtask_id=qtask_id,
					operation=operation, eviction=eviction)
			return self._completion_not_ready_response_locked(
				reservation_id, cid=cid, qtask_id=qtask_id,
				operation=operation)
		if not consume:
			return self._completion_public_record(record, operation)
		self._remove_completion_record_locked(
			queue, record, reason="read", now_ns=time.time_ns())
		record["cq_dequeue_time"] = time.time()
		record["qpm_cq_dequeue_time_ns"] = time.time_ns()
		queue.dequeued_records.append({
			"cid": record.get("cid"),
			"qtask_id": record.get("qtask_id"),
			"reservation_id": reservation_id,
			"dequeue_time_ns": record["qpm_cq_dequeue_time_ns"],
			"operation": operation,
		})
		if record.get("cid") is not None:
			self.forget_terminal_task_for_cid(record["cid"])
		self._purge_completion_queues_locked(time.time_ns())
		return self._completion_public_record(record, operation)

	def _completion_queue_for_poll_locked(self, reservation_id, operation,
					      cid=None, qtask_id=None):
		if reservation_id is None:
			return None, {
				"outcome": "INVALID_RESERVATION",
				"lifecycle_state": "invalid-reservation",
				"reason": "reservation-required",
				"message": (
					"reservation_id is required for managed "
					"completion polling"),
				"completion_ready": False,
				"poll_operation": operation,
			}
		queue = self.completion_queues.get(reservation_id)
		if queue is not None:
			return queue, None
		eviction = self._completion_eviction_for_selector_locked(
			reservation_id, cid=cid, qtask_id=qtask_id)
		if eviction is not None:
			return None, self._completion_no_longer_retained_response(
				reservation_id, cid=cid, qtask_id=qtask_id,
				operation=operation, eviction=eviction)
		try:
			get_reservation(self.admission_context, reservation_id)
		except Exception as error:
			return None, {
				"outcome": "MISSING_RESERVATION",
				"lifecycle_state": "missing-reservation",
				"reservation_id": reservation_id,
				"reason": "missing-reservation",
				"message": str(error),
				"completion_ready": False,
				"poll_operation": operation,
			}
		return self._ensure_completion_queue_locked(reservation_id), None

	def _completion_selector_reservation_error_locked(
			self, reservation_id, cid=None, qtask_id=None, record=None):
		runtime = self._runtime_for_task_selector_locked(
			cid=cid, qtask_id=qtask_id)
		if runtime is None:
			runtime = self._terminal_task_for_selector_locked(
				cid=cid, qtask_id=qtask_id)
		if runtime is None:
			return None
		return self._task_reservation_error_locked(
			runtime, reservation_id, require_reservation=True)

	def _completion_record_for_selector_locked(self, queue, cid=None,
						   qtask_id=None):
		if cid is None and qtask_id is None:
			return queue.records[0] if queue.records else None
		candidates = None
		if cid is not None:
			candidates = queue.records_by_cid.get(cid, [])
		if qtask_id is not None:
			qtask_candidates = queue.records_by_qtask_id.get(qtask_id, [])
			candidates = (
				qtask_candidates if candidates is None else
				[
					record for record in candidates
					if record in qtask_candidates
				])
		if not candidates:
			return None
		return candidates[0]

	def _completion_not_ready_response_locked(self, reservation_id, cid=None,
						  qtask_id=None,
						  operation="peek_cq"):
		runtime = self._runtime_for_task_selector_locked(
			cid=cid, qtask_id=qtask_id)
		if runtime is None:
			runtime = self._terminal_task_for_selector_locked(
				cid=cid, qtask_id=qtask_id)
		reason = "completion-not-ready"
		if runtime is not None:
			status = self._task_status_locked(runtime, reason=reason)
		else:
			status = {
				"outcome": "IN_PROGRESS",
				"lifecycle_state": "no-ready-completion",
				"reservation_id": reservation_id,
				"cid": cid,
				"qtask_id": qtask_id,
				"reason": reason,
			}
		status["completion_ready"] = False
		status["poll_operation"] = operation
		return {key: value for key, value in status.items()
			if value is not None}

	def _completion_no_longer_retained_response(
			self, reservation_id, cid=None, qtask_id=None,
			operation="peek_cq", eviction=None):
		response = {
			"outcome": "NO_LONGER_RETAINED",
			"lifecycle_state": "no-longer-retained",
			"reservation_id": reservation_id,
			"cid": cid,
			"qtask_id": qtask_id,
			"reason": "completion-no-longer-retained",
			"message": "completion record is no longer retained",
			"completion_ready": False,
			"poll_operation": operation,
		}
		if isinstance(eviction, dict):
			for key in ("evicted_at_ns", "retention_reason"):
				if key in eviction:
					response[key] = eviction[key]
		return {key: value for key, value in response.items()
			if value is not None}

	def _completion_eviction_for_selector_locked(
			self, reservation_id, cid=None, qtask_id=None):
		evictions = self.completion_evictions_by_reservation.get(
			reservation_id, {})
		for selector in _completion_selectors(
				reservation_id=reservation_id, cid=cid,
				qtask_id=qtask_id):
			if selector in evictions:
				return evictions[selector]
		return evictions.get(("reservation", reservation_id))

	def _completion_public_record(self, record, operation):
		result = {
			key: deepcopy(value)
			for key, value in record.items()
			if not key.startswith("_qpm_")
		}
		result["completion_ready"] = True
		result["poll_operation"] = operation
		return result

	def _completion_index_add_locked(self, queue, record):
		cid = record.get("cid")
		if cid is not None:
			queue.records_by_cid.setdefault(cid, []).append(record)
		qtask_id = record.get("qtask_id")
		if qtask_id is not None:
			queue.records_by_qtask_id.setdefault(qtask_id, []).append(record)

	def _completion_index_remove_locked(self, queue, record):
		for key, mapping in (
				(record.get("cid"), queue.records_by_cid),
				(record.get("qtask_id"), queue.records_by_qtask_id)):
			if key is None:
				continue
			records = mapping.get(key)
			if records is None:
				continue
			if record in records:
				records.remove(record)
			if not records:
				mapping.pop(key, None)

	def _remove_completion_record_locked(self, queue, record, reason, now_ns):
		try:
			queue.records.remove(record)
		except ValueError:
			pass
		self._completion_index_remove_locked(queue, record)
		queue.retained_bytes = max(
			0, queue.retained_bytes -
			record.get("_qpm_record_size_bytes", 0))
		if reason != "read":
			self._remember_completion_eviction_locked(
				queue, record, reason, now_ns)

	def _remember_completion_eviction_locked(self, queue, record, reason,
						 now_ns):
		eviction = {
			"evicted_at_ns": now_ns,
			"retention_reason": reason,
		}
		evictions = self.completion_evictions_by_reservation.setdefault(
			queue.reservation_id, {})
		for selector in _completion_selectors(
				reservation_id=queue.reservation_id,
				cid=record.get("cid"),
				qtask_id=record.get("qtask_id")):
			queue.evicted_selectors[selector] = dict(eviction)
			evictions[selector] = dict(eviction)

	def _enforce_completion_retention_locked(self, queue, now_ns):
		for record in list(queue.records):
			if not self._completion_record_expired_locked(record, now_ns):
				continue
			self._remove_completion_record_locked(
				queue, record, "completion-ttl-expired", now_ns)
		max_records = self.completion_retention[
			"max_records_per_reservation"]
		while max_records >= 0 and len(queue.records) > max_records:
			self._remove_completion_record_locked(
				queue, queue.records[0], "max-records-exceeded",
				now_ns)
		max_bytes = self.completion_retention[
			"max_bytes_per_reservation"]
		while max_bytes >= 0 and queue.retained_bytes > max_bytes:
			self._remove_completion_record_locked(
				queue, queue.records[0], "max-bytes-exceeded",
				now_ns)

	def _completion_record_expired_locked(self, record, now_ns):
		ttl_seconds = self.completion_retention["completion_ttl_seconds"]
		if ttl_seconds < 0:
			return False
		return (
			record.get("qpm_cq_enqueue_time_ns", 0) +
			int(ttl_seconds * 1_000_000_000) <= now_ns)

	def _purge_completion_queues_locked(self, now_ns, force=False):
		purge_interval_ns = int(
			self.completion_retention["purge_interval_seconds"] *
			1_000_000_000)
		if (not force and purge_interval_ns > 0 and
				self.completion_last_purge_ns and
				now_ns - self.completion_last_purge_ns < purge_interval_ns):
			return {"purged_reservations": [], "evicted_records": 0}
		self.completion_last_purge_ns = now_ns
		summary = {"purged_reservations": [], "evicted_records": 0}
		for reservation_id, queue in list(self.completion_queues.items()):
			before = len(queue.records)
			self._enforce_completion_retention_locked(queue, now_ns)
			summary["evicted_records"] += before - len(queue.records)
			if not self._completion_queue_collectable_locked(
					queue, now_ns):
				continue
			self._remember_completion_queue_gc_locked(queue, now_ns)
			self.completion_queues.pop(reservation_id, None)
			summary["purged_reservations"].append(reservation_id)
		return summary

	def _completion_queue_collectable_locked(self, queue, now_ns):
		if queue.terminal_at_ns is None:
			return False
		if self._reservation_has_active_work_locked(queue.reservation_id):
			return False
		terminal_retention_ns = int(
			self.completion_retention[
				"terminal_reservation_retention_seconds"] *
			1_000_000_000)
		if terminal_retention_ns >= 0:
			deadline = queue.terminal_at_ns + terminal_retention_ns
			if deadline <= now_ns:
				for record in list(queue.records):
					self._remove_completion_record_locked(
						queue, record,
						"terminal-reservation-retention-expired",
						now_ns)
		return not queue.records

	def _reservation_has_active_work_locked(self, reservation_id):
		for qtask_id in self.qtask_ids_by_reservation.get(
				reservation_id, ()):
			if self._qtask_has_active_work_locked(qtask_id):
				return True
		for qtask_id, pending in self.pending_capacity.items():
			if (pending.get("reservation_id") == reservation_id and
					self._qtask_has_active_work_locked(qtask_id)):
				return True
		for qtask_id, hold in self.capacity_holds.items():
			if (hold.get("reservation_id") == reservation_id and
					self._qtask_has_active_work_locked(qtask_id)):
				return True
		return False

	def _qtask_has_active_work_locked(self, qtask_id):
		runtime = self.runtime_by_qtask_id.get(qtask_id)
		if runtime is None:
			runtime = self.terminal_tasks_by_qtask_id.get(qtask_id)
		if runtime is None:
			return False
		return runtime.state not in QPM_TASK_TERMINAL_STATES

	def _remember_completion_queue_gc_locked(self, queue, now_ns):
		evictions = self.completion_evictions_by_reservation.setdefault(
			queue.reservation_id, {})
		evictions[("reservation", queue.reservation_id)] = {
			"evicted_at_ns": now_ns,
			"retention_reason": "terminal-reservation-garbage-collected",
		}

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
		normalized.setdefault(
			"max_qubits", max_qubits or baseline["qubit_count"])
		normalized.setdefault("external_device_id", self.config.target_id)
		normalized.setdefault("baseline", baseline)
		normalized.setdefault("max_shots", 1)
		normalized.setdefault("one_q_gate_ns", 1)
		normalized.setdefault("two_q_gate_ns", 1)
		normalized.setdefault("measurement_ns", 1)
		normalized.setdefault("default_ttl_ns", 60_000_000_000)
		return normalized

	def _normalize_admission_configuration(self, configuration,
					       device_id=None):
		configuration = deepcopy(configuration or {})
		if not isinstance(configuration, dict):
			raise QPMAdmissionValidationError(
				"admission configuration must be a mapping")
		profile = deepcopy(self.device_profile)
		if profile is None:
			raise QPMAdmissionValidationError(
				"device profile must be configured before admission policy")
		configured_device_id = configuration.get(
			"device_id", device_id if device_id is not None else profile["device_id"])
		if device_id is not None and configured_device_id != device_id:
			raise QPMAdmissionValidationError(
				"admission configuration device_id does not match request")
		if configured_device_id != profile["device_id"]:
			raise QPMAdmissionValidationError(
				"admission configuration does not match the device profile")

		policy = deepcopy(configuration.get("policy") or {})
		if not isinstance(policy, dict):
			raise QPMAdmissionValidationError("policy must be a mapping")
		policy_name = policy.get("name")
		if policy_name not in ("unlimited", "credit", "rate"):
			raise QPMAdmissionValidationError(
				f"unsupported admission policy: {policy_name!r}")
		policy_options = policy.get("options") or {}
		if not isinstance(policy_options, dict):
			raise QPMAdmissionValidationError("policy options must be a mapping")
		policy = {"name": policy_name, "options": dict(policy_options)}

		estimator = deepcopy(configuration.get("estimator") or {
			"name": "baseline", "options": {}})
		if not isinstance(estimator, dict):
			raise QPMAdmissionValidationError("estimator must be a mapping")
		if estimator.get("name") != "baseline":
			raise QPMAdmissionValidationError(
				"only the baseline admission estimator is supported")
		estimator_options = estimator.get("options") or {}
		if estimator_options:
			raise QPMAdmissionValidationError(
				"baseline estimator options are not supported; configure "
				"the complete baseline circuit instead")
		estimator = {"name": "baseline", "options": {}}

		baseline = deepcopy(
			configuration.get("baseline") or profile.get("baseline") or {})
		baseline_fields = (
			"qubit_count", "depth", "one_q_gate_count", "two_q_gate_count",
			"shots", "measurement_count")
		missing = [
			field_name for field_name in baseline_fields
			if field_name not in baseline
		]
		if missing:
			raise QPMAdmissionValidationError(
				"baseline circuit is missing fields: " + ", ".join(missing))
		baseline = {
			field_name: _nonnegative_config_int(
				baseline[field_name], field_name)
			for field_name in baseline_fields
		}
		for field_name in (
			"qubit_count", "depth", "shots", "measurement_count"
		):
			if baseline[field_name] == 0:
				raise QPMAdmissionValidationError(
					f"baseline {field_name} must be greater than zero")

		capacity = deepcopy(configuration.get("capacity") or {})
		if not isinstance(capacity, dict):
			raise QPMAdmissionValidationError("capacity must be a mapping")
		capacity = self._normalize_policy_capacity(
			policy_name, capacity, profile)
		profile["baseline"] = baseline
		profile.update(capacity)
		normalized = {
			"device_id": configured_device_id,
			"policy": policy,
			"estimator": estimator,
			"baseline": baseline,
			"capacity": capacity,
		}
		if "expected_version" in configuration:
			normalized["expected_version"] = _nonnegative_config_int(
				configuration["expected_version"], "expected_version")
		return normalized, self._normalize_device_profile(profile)

	def _normalize_policy_capacity(self, policy_name, capacity, profile):
		if policy_name == "unlimited":
			if capacity:
				raise QPMAdmissionValidationError(
					"unlimited policy does not accept capacity settings")
			return {}
		if policy_name == "credit":
			unknown = set(capacity) - {"total_credits"}
			if unknown:
				raise QPMAdmissionValidationError(
					"credit capacity has unsupported fields: " +
					", ".join(sorted(unknown)))
			value = capacity.get("total_credits", profile.get("total_credits"))
			value = _positive_config_int(value, "total_credits")
			return {"total_credits": value}
		unknown = set(capacity) - {"device_rate", "time_span_ns"}
		if unknown:
			raise QPMAdmissionValidationError(
				"rate capacity has unsupported fields: " +
				", ".join(sorted(unknown)))
		rate = _positive_config_int(
			capacity.get("device_rate", profile.get("device_rate")),
			"device_rate")
		window = _positive_config_int(
			capacity.get("time_span_ns", profile.get("time_span_ns")),
			"time_span_ns")
		return {"device_rate": rate, "time_span_ns": window}

	def _restore_admission_configuration(self, configuration, profile):
		if not configuration or profile is None:
			return
		try:
			register_device_profile(self.admission_context, profile)
			set_estimator(
				self.admission_context, configuration["device_id"],
				configuration["estimator"], policy=configuration["policy"],
				device_profile=profile)
			set_policy(
				self.admission_context, configuration["device_id"],
				configuration["policy"], estimator=configuration["estimator"],
				device_profile=profile)
		except Exception:
			pass

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
		stored_result = self.result_state.get(runtime.qtask_id)
		if (reason is None and runtime.state == QPM_TASK_FAILED and
				isinstance(stored_result, dict)):
			reason = stored_result.get("reason")
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
			if runtime.state == QPM_TASK_FAILED:
				response["error"] = result
			else:
				response["result"] = result
		elif runtime.state == QPM_TASK_FAILED and stored_result is not None:
			response["error"] = (
				dict(stored_result)
				if isinstance(stored_result, dict) else stored_result)
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
			provider_cancel = (
				self._cancel_provider_for_task_locked(runtime)
				if self._runtime_provider_active_locked(runtime) else None)
			if provider_cancel is not None:
				fault["provider_cancel_status"] = provider_cancel["status"]
				fault["provider_cancel_reason"] = provider_cancel["reason"]
				if runtime.provider_handle is not None:
					fault["provider_handle"] = runtime.provider_handle
				if not provider_cancel["terminal"]:
					fault["reason"] = (
						"inactive-reservation-hold-provider-cancel-pending")
					record = self._record_reconciliation_fault_locked(fault)
					summary["capacity_hold_faults"].append(dict(record))
					continue
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

	def _runtime_provider_active_locked(self, runtime):
		if runtime is None:
			return False
		return (
			runtime.provider_handle is not None or
			runtime.qtask_id in self.provider_inflight)

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

	def _defw_directory_event_records_locked(
			self, event_type, service_record=None, peer_event=None,
			reason=None, details=None):
		event_type = _normalize_directory_event_type(event_type)
		service_record = dict(service_record or {})
		peer_event = dict(peer_event or {})
		reason = (
			reason or peer_event.get("reason") or
			service_record.get("down_reason") or None)
		base_details = self._defw_directory_event_details(
			service_record, peer_event, details)
		records = []
		if event_type in ("register", "registration", "service-registration"):
			records.append(self._defw_directory_lifecycle_record(
				"service-registration", reason=reason,
				details=base_details))
			generation = _int_or_none(service_record.get("generation"))
			previous_generation = _int_or_none(
				base_details.get("previous_generation"))
			if (previous_generation is not None and generation is not None and
					previous_generation != generation):
				records.append(self._defw_directory_lifecycle_record(
					"service-restart", reason="runtime-restart",
					details=base_details))
				records.append(self._defw_directory_lifecycle_record(
					"generation-change", reason="generation-change",
					details=dict(
						base_details,
						previous_generation=previous_generation,
						current_generation=generation)))
			elif generation is not None and generation > 1:
				records.append(self._defw_directory_lifecycle_record(
					"service-restart", reason="runtime-restart",
					details=base_details))
				records.append(self._defw_directory_lifecycle_record(
					"generation-change", reason="generation-increment",
					details=dict(
						base_details,
						current_generation=generation)))
			return records
		if event_type in ("deregister", "deregistration",
				  "service-deregistration"):
			return [self._defw_directory_lifecycle_record(
				"service-deregistration", reason=reason,
				details=base_details)]
		if event_type in ("peer-lost", "peer-loss"):
			records.append(self._defw_directory_lifecycle_record(
				"peer-lost", reason=reason, details=base_details))
			if (reason == "heartbeat-timeout" or
					service_record.get("state") == "TIMED_OUT"):
				records.append(self._defw_directory_lifecycle_record(
					"service-timeout", reason=reason,
					details=base_details))
			return records
		if event_type in ("peer-ready", "service-recovered"):
			return [self._defw_directory_lifecycle_record(
				"service-recovered", reason=reason,
				details=base_details)]
		if event_type in ("purge", "retention-purge"):
			return [self._defw_directory_lifecycle_record(
				"retention-purge", reason=reason,
				details=base_details)]
		if event_type in ("restart", "service-restart"):
			return [self._defw_directory_lifecycle_record(
				"service-restart", reason=reason,
				details=base_details)]
		if event_type == "generation-change":
			return [self._defw_directory_lifecycle_record(
				"generation-change", reason=reason,
				details=base_details)]
		return [self._defw_directory_lifecycle_record(
			event_type or "directory-lifecycle", reason=reason,
			details=base_details)]

	def _defw_directory_lifecycle_record(self, event, reason=None,
					     details=None):
		record = {
			"event": event,
			"target_id": self.config.target_id,
			"source": "defw-directory",
			"timestamp_ns": time.time_ns(),
		}
		if reason:
			record["reason"] = reason
		if details:
			record["details"] = dict(details)
		return record

	def _defw_directory_event_details(
			self, service_record, peer_event, details):
		event_details = {}
		for key in (
				"service_id", "service_name", "service_type",
				"runtime_id", "peer_handle", "generation", "state",
				"down_reason", "retention_deadline"):
			value = service_record.get(key)
			if value is not None:
				event_details[key] = value
		if peer_event:
			for key in (
					"event_type", "peer_handle", "runtime_id",
					"remote_runtime_id", "reason", "timestamp"):
				value = peer_event.get(key)
				if value is not None:
					event_details[f"peer_{key}"] = value
		if details:
			event_details.update(dict(details))
		return event_details

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

	def _active_scheduler_task_count_locked(self):
		return sum(
			1 for runtime in self.runtime_by_qtask_id.values()
			if (
				runtime.scheduler_task_id is not None and
				runtime.state not in QPM_TASK_TERMINAL_STATES))

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
			for field_name in (
					"estimated_ns", "baseline_units",
					"credits", "rate_units"):
				totals[field_name] += usage.get(field_name, 0)
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
		workload_kind = (
			request.get("workload_kind") or
			request.get("workload_type") or
			workload.get("kind") or
			workload.get("type") or
			"quantum")
		walltime_ns = request.get("walltime_ns", 0)
		ttl_ns = request.get(
			"ttl_ns", request.get("expiration_ttl_ns", 0))
		metadata = {
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
			"workload_kind": workload_kind,
			"run_context": run_context,
			"walltime_ns": walltime_ns,
			"ttl_ns": ttl_ns,
			"task_class": dict(task_class),
		}
		binding = self._reservation_binding_metadata(
			request=request,
			owner=owner,
			workload=workload,
			run_context=run_context,
			device_external=device_external,
			device_id=device_id,
			scope_external=scope_external,
			scope_id=scope_id,
			user_external=user_external,
			user_id=user_id,
			job_external=job_external,
			job_id=job_id,
			workload_kind=workload_kind,
			walltime_ns=walltime_ns,
			ttl_ns=ttl_ns,
			task_class=task_class,
			operation=operation)
		metadata["reservation_binding"] = binding
		credential_binding = binding.get("provider_credential_binding")
		if credential_binding:
			metadata["provider_credential_binding"] = credential_binding
		return {
			"request_id": request.get(
				"request_id", self._allocate_admission_request_id()),
			"device_id": device_id,
			"user_id": user_id,
			"job_id": job_id,
			"scope_id": scope_id,
			"reservation_id": request.get("reservation_id", 0),
			"workload_kind": workload_kind,
			"walltime_ns": walltime_ns,
			"ttl_ns": ttl_ns,
			"classical_runtime_ns": request.get("classical_runtime_ns", 0),
			"overhead_ns": request.get("overhead_ns", 0),
			"priority": request.get("priority", 0),
			"task_class": task_class,
			"metadata": metadata,
		}

	def _reservation_binding_metadata(
			self, request, owner, workload, run_context, device_external,
			device_id, scope_external, scope_id, user_external, user_id,
			job_external, job_id, workload_kind, walltime_ns, ttl_ns,
			task_class, operation):
		launcher = dict(request.get("launcher", {}))
		if "scheduler" not in launcher:
			value = request.get("scheduler") or request.get("launcher_name")
			if value is not None:
				launcher["scheduler"] = value
		launcher.update({
			"user_id": user_id,
			"external_user_id": user_external,
			"job_id": job_id,
			"external_job_id": job_external,
			"allocation_id": request.get("allocation_id"),
			"project_id": request.get("project_id"),
			"session_id": request.get("session_id"),
			"scope_id": scope_id,
			"external_scope_id": scope_external,
		})
		resource = {
			"device_id": device_id,
			"target_device_id": device_external,
			"scope_id": scope_id,
			"external_scope_id": scope_external,
			"workload_kind": workload_kind,
			"walltime_ns": walltime_ns,
			"ttl_ns": ttl_ns,
			"task_class": dict(task_class),
		}
		binding = {
			"schema": RESERVATION_BINDING_SCHEMA,
			"owner": _redacted_metadata(owner),
			"launcher": _drop_none(_redacted_metadata(launcher)),
			"resource": _drop_none(_redacted_metadata(resource)),
			"allowed_operations": _allowed_reservation_operations(
				request, operation),
			"analytics": self._reservation_analytics_metadata(
				request, workload, run_context, operation),
		}
		credential_binding = self._provider_credential_binding(
			request, workload, device_external, scope_external,
			user_external)
		if credential_binding:
			binding["provider_credential_binding"] = credential_binding
		return binding

	def _reservation_analytics_metadata(
			self, request, workload, run_context, operation):
		analytics = dict(request.get("analytics", {}))
		for key in (
				"application",
				"workflow_id",
				"frontend",
				"campaign_id",
				"operation_label",
				"site_tags"):
			if key in request and key not in analytics:
				analytics[key] = request[key]
		for key, value in (
				("application", workload.get("application") or
				 workload.get("example")),
				("frontend", workload.get("frontend")),
				("operation", operation or run_context.get("operation")),
				("workflow_id", workload.get("workflow_id"))):
			if value is not None and key not in analytics:
				analytics[key] = value
		return _drop_none(_redacted_metadata(analytics))

	def _provider_credential_binding(
			self, request, workload, device_external, scope_external,
			user_external):
		binding = {}
		for key in (
				"provider_credential_binding",
				"credential_binding",
				"credential_context"):
			value = request.get(key)
			if isinstance(value, dict):
				binding.update(_redacted_metadata(value))
		for target_key, source_key in (
				("provider", "credential_provider"),
				("credential_hint", "credential_hint"),
				("credential_handle", "credential_handle"),
				("credential_scope", "credential_scope")):
			value = request.get(source_key)
			if value is not None:
				binding[target_key] = _redacted_metadata(value)
		if "provider" not in binding:
			provider = (
				request.get("provider") or
				request.get("backend") or
				workload.get("backend") or
				workload.get("provider"))
			if provider is not None:
				binding["provider"] = provider
		if not binding:
			return {}
		binding.setdefault("target_device_id", device_external)
		binding.setdefault("scope_id", scope_external)
		binding.setdefault("user_id", user_external)
		binding.setdefault("secret_material", "not-stored")
		return _drop_none(binding)

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
			"one_q_gate_count": task_class.get(
				"one_q_gate_count", request.get("one_q_gate_count", 0)),
			"two_q_gate_count": task_class.get(
				"two_q_gate_count", request.get("two_q_gate_count", 0)),
			"shots": shots,
			"measurement_count": task_class.get(
				"measurement_count", request.get("measurement_count", 1)),
		}

	def _admission_filters(self, filters):
		filters = dict(filters or {})
		for field_name, kind in (
			("device_id", "device_id"),
			("scope_id", "scope_id"),
			("user_id", "user_id"),
			("job_id", "job_id"),
		):
			if field_name in filters and filters[field_name] is not None:
				filters[field_name] = self.canonicalize_external_id(
					kind, filters[field_name])
		return filters

	def _allocate_admission_request_id(self):
		request_id = self.admission_request_id_next
		self.admission_request_id_next += 1
		return request_id

	def _estimated_usage(self, circuit, runtime):
		info = circuit.info
		shots = info.get("num_shots", info.get("shots", 1))
		task_class = self._admission_task_class(info)
		estimate = estimate_admission_qtask_class(
			self.admission_context, self._device_id(), task_class)
		if estimate is not None:
			info["admission_estimate"] = dict(estimate)
			estimated_ns = _estimate_total_ns(estimate)
			baseline_units = _estimate_baseline_units(estimate)
			if estimated_ns:
				info["estimated_ns"] = estimated_ns
				info["estimated_device_ns"] = estimated_ns
			if baseline_units:
				info["baseline_units"] = baseline_units
				info.setdefault("estimated_cost", baseline_units)
		else:
			estimated_ns = info.get(
				"estimated_ns",
				info.get("walltime_ns", info.get("estimated_device_ns", 0)))
			baseline_units = info.get("baseline_units", max(1, shots))
		return {
			"reservation_id": runtime.reservation_id,
			"task_id": runtime.qtask_id,
			"class_id": task_class.get("class_id", 1),
			"event_time_ns": time.time_ns(),
			"estimated_ns": estimated_ns,
			"baseline_units": baseline_units,
			"credits": info.get(
				"credits",
				info.get("estimated_credits", baseline_units)),
			"rate_units": info.get(
				"rate_units",
				info.get("estimated_rate_units", baseline_units)),
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
		for field_name in ("baseline_units", "credits", "rate_units"):
			actual[f"actual_{field_name}"] = self._actual_capacity_value(
				circuit, usage, field_name)
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

	def _actual_capacity_value(self, circuit, usage, field_name):
		info = getattr(circuit, "info", {}) if circuit is not None else {}
		for key in (
			f"actual_{field_name}", f"observed_{field_name}"
		):
			if key in info:
				return info[key]
			if key in usage:
				return usage[key]
		if circuit is not None and circuit.completion_time >= 0:
			return usage.get(field_name, 0)
		return 0

	def _unused_usage(self, usage, actual):
		unused = dict(usage)
		for field_name, actual_field in (
				("estimated_ns", "observed_device_ns"),
				("baseline_units", "actual_baseline_units"),
				("credits", "actual_credits"),
				("rate_units", "actual_rate_units")):
			estimated = usage.get(field_name, 0) or 0
			actual_value = actual.get(actual_field, 0) or 0
			unused[field_name] = max(0, estimated - actual_value)
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
		limit = self._effective_dispatch_limit_locked()
		return limit == 0 or len(self.provider_inflight) < limit

	def _effective_dispatch_limit_locked(self):
		operator_limit = max(0, int(
			self.scheduler_control.get("dispatch_depth", 0) or 0))
		device_limit = 0
		if self.device_profile is not None:
			device_limit = max(0, int(
				self.device_profile.get("max_provider_queue_depth", 0) or 0))
		limits = [value for value in (operator_limit, device_limit) if value]
		return min(limits) if limits else 0

	def _dispatch_limits_locked(self):
		device_limit = 0
		if self.device_profile is not None:
			device_limit = max(0, int(
				self.device_profile.get("max_provider_queue_depth", 0) or 0))
		return {
			"max_inflight": max(0, int(
				self.scheduler_control.get("dispatch_depth", 0) or 0)),
			"max_provider_queue_depth": device_limit,
			"effective_max_inflight": self._effective_dispatch_limit_locked(),
			"provider_inflight": len(self.provider_inflight),
		}

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

	def _require_reservation_matches_context_locked(
			self, reservation, request_context):
		request_metadata = dict(
			getattr(request_context, "metadata", None) or {})
		reservation_metadata = self._reservation_metadata_locked(
			reservation)
		reservation_id = reservation.get("reservation_id")
		for binding in (
				("device_id", "device_id",
				 ("device_id", "target_device_id", "external_device_id"),
				 ("external_device_id", "target_device_id")),
				("scope_id", "scope_id",
				 ("scope_id", "external_scope_id"),
				 ("external_scope_id", "scope_id")),
				("job_id", "job_id",
				 ("job_id", "allocation_id", "external_job_id",
				  "external_allocation_id"),
				 ("external_job_id", "job_id", "external_allocation_id",
				  "allocation_id")),
				("session_id", None,
				 ("session_id", "external_session_id"),
				 ("external_session_id", "session_id")),
		):
			field_name, record_field, request_keys, metadata_keys = binding
			request_value = _metadata_identifier_any(
				request_metadata, *request_keys)
			if request_value is None:
				continue
			expected_value = self._reservation_binding_value_locked(
				reservation, reservation_metadata, record_field,
				metadata_keys, field_name)
			if expected_value is None:
				continue
			request_value = self._canonical_binding_value(
				field_name, request_value)
			if request_value != expected_value:
				raise QPMAdmissionValidationError(
					f"reservation {field_name} mismatch: "
					f"reservation_id={reservation_id} "
					f"expected={expected_value} requested={request_value}")
		self._require_reservation_operation_matches_locked(
			reservation_id, reservation_metadata, request_metadata)

	def _reservation_binding_value_locked(
			self, reservation, metadata, record_field, metadata_keys,
			field_name):
		if record_field is not None and reservation.get(record_field) is not None:
			return self._canonical_binding_value(
				field_name, reservation.get(record_field))
		value = _metadata_identifier_any(metadata, *metadata_keys)
		if value is None:
			return None
		return self._canonical_binding_value(field_name, value)

	def _canonical_binding_value(self, field_name, value):
		if isinstance(value, int) and not isinstance(value, bool):
			return value
		return self.canonicalize_external_id(field_name, value)

	def _require_reservation_operation_matches_locked(
			self, reservation_id, reservation_metadata, request_metadata):
		expected = _operation_from_metadata(reservation_metadata)
		requested = _operation_from_metadata(request_metadata)
		if expected is None or requested is None:
			return
		if _operation_names_compatible(expected, requested):
			return
		raise QPMAdmissionValidationError(
			f"reservation operation mismatch: "
			f"reservation_id={reservation_id} "
			f"expected={expected} requested={requested}")

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
			       now_ns=None, deliveries=None):
		if close_kind == "expire":
			return self._close_expired_reservations(
				reservation_id, now_ns=now_ns,
				deliveries=deliveries)
		now_ns = now_ns or time.time_ns()
		close_state = self._reservation_close_state_locked(
			reservation_id, close_kind, now_ns)
		self._remove_pending_for_reservation(
			reservation_id, close_state, deliveries=deliveries)
		self._reconcile_holds_for_reservation(
			reservation_id, close_state, deliveries=deliveries)
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
		else:
			raise QPMAdmissionValidationError(
				f"unsupported reservation close kind: {close_kind}")
		close_state["completed_at_ns"] = time.time_ns()
		close_state["status"] = result.get("status", "accepted")
		if close_state["status"] == "accepted":
			self._mark_completion_queue_terminal_locked(
				reservation_id, close_state["completed_at_ns"])
		return result

	def _close_expired_reservations(self, reservation_id, now_ns=None,
					deliveries=None):
		now_ns = now_ns or time.time_ns()
		expired_reservation_ids = self._expired_active_reservation_ids_locked(
			reservation_id, now_ns)
		if not expired_reservation_ids:
			expired_reservation_ids = [reservation_id]
		close_states = []
		for expired_reservation_id in expired_reservation_ids:
			close_state = self._reservation_close_state_locked(
				expired_reservation_id, "expire", now_ns)
			close_states.append((expired_reservation_id, close_state))
		for expired_reservation_id, close_state in close_states:
			self._remove_pending_for_reservation(
				expired_reservation_id, close_state,
				deliveries=deliveries)
			self._reconcile_holds_for_reservation(
				expired_reservation_id, close_state,
				deliveries=deliveries)
			self._refresh_provider_cancel_pending(
				expired_reservation_id, close_state)
		pending_qtask_ids = []
		pending_reservation_ids = []
		for expired_reservation_id, close_state in close_states:
			if not close_state["provider_cancel_pending"]:
				continue
			close_state["status"] = "provider-cancel-pending"
			pending_reservation_ids.append(expired_reservation_id)
			pending_qtask_ids.extend(close_state["provider_cancel_pending"])
		if pending_qtask_ids:
			return {
				"status": "pending",
				"reservation_id": reservation_id,
				"reason": "provider-cancel-pending",
				"pending_reservation_ids": pending_reservation_ids,
				"pending_qtask_ids": pending_qtask_ids,
			}
		expire_reservations(self.admission_context, now_ns)
		for expired_reservation_id, close_state in close_states:
			close_state["completed_at_ns"] = time.time_ns()
			close_state["status"] = "accepted"
			self.reservation_metadata_by_id.pop(expired_reservation_id, None)
			self._remove_provider_credential_locked(expired_reservation_id)
			self._mark_completion_queue_terminal_locked(
				expired_reservation_id, close_state["completed_at_ns"])
		return {
			"status": "accepted",
			"reservation_id": reservation_id,
			"reason": "expired",
		}

	def _expired_active_reservation_ids_locked(self, reservation_id, now_ns):
		reservations = []
		try:
			reservations = list_reservations(self.admission_context, {})
		except Exception:
			pass
		if reservation_id is not None:
			try:
				requested = get_reservation(
					self.admission_context, reservation_id)
			except Exception:
				requested = None
			if requested is not None:
				reservations.append(requested)
		expired_reservation_ids = []
		seen = set()
		for reservation in reservations:
			if reservation.get("state") != "active":
				continue
			expires_at_ns = reservation.get("expires_at_ns")
			if not expires_at_ns or expires_at_ns > now_ns:
				continue
			expired_reservation_id = reservation.get("reservation_id")
			if expired_reservation_id in seen:
				continue
			seen.add(expired_reservation_id)
			expired_reservation_ids.append(expired_reservation_id)
		return expired_reservation_ids

	def _reservation_close_state_locked(
			self, reservation_id, close_kind, now_ns):
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
		return close_state

	def _remove_pending_for_reservation(self, reservation_id, close_state,
					    deliveries=None):
		for qtask_id, pending in list(self.pending_capacity.items()):
			if pending["reservation_id"] != reservation_id:
				continue
			self.pending_capacity.pop(qtask_id, None)
			runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is not None:
				cancelled = self._cancel_runtime_for_reservation_close(
					runtime, close_state)
				if cancelled and deliveries is not None:
					self._append_terminal_completion_delivery_locked(
						deliveries, runtime,
						self._task_status_locked(
							runtime, outcome="CANCELLED",
							reason=close_state["reason"]))
			close_state["pending_removed"].append(qtask_id)

	def _reconcile_holds_for_reservation(self, reservation_id, close_state,
					     deliveries=None):
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
			if runtime is not None and deliveries is not None:
				self._append_terminal_completion_delivery_locked(
					deliveries, runtime,
					self._task_status_locked(
						runtime, outcome="CANCELLED",
						reason=close_state["reason"]))

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
		credential_mode=os.environ.get(
			"QFW_QPM_CREDENTIAL_MODE", "no-secret"),
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


def find_target_controller(target_id):
	with _CONTROLLERS_LOCK:
		return _CONTROLLERS.get(target_id)


def _clear_target_controllers_for_tests():
	"""Reset process-global controllers for isolated unit tests."""
	with _CONTROLLERS_LOCK:
		controllers = list(_CONTROLLERS.values())
		_CONTROLLERS.clear()
	for controller in controllers:
		controller.clear_provider_credentials()
		controller.stop_completion_purge_worker()


def completion_retention_config():
	retention = dict(COMPLETION_RETENTION_DEFAULTS)
	retention.update(_completion_retention_config_file())
	return retention


def _completion_retention_config_file():
	path = os.environ.get(SITE_CONFIG_ENV, "").strip()
	if not path or not os.path.exists(path):
		return {}
	try:
		import yaml
		with open(path, "r", encoding="utf-8") as stream:
			data = yaml.safe_load(stream) or {}
	except Exception:
		return {}
	block = (
		data.get("qpm", {})
		.get("completion-queues", {})
		.get("retention", {}))
	if not isinstance(block, dict):
		return {}
	retention = {}
	for config_key, internal_key in COMPLETION_RETENTION_CONFIG_KEYS.items():
		if config_key not in block:
			continue
		retention[internal_key] = _completion_retention_int(
			block[config_key],
			f"qpm.completion-queues.retention.{config_key}")
	return retention


def _completion_retention_int(value, source):
	if isinstance(value, bool):
		raise ValueError(
			f"invalid QPM completion retention value for {source}: "
			f"{value!r}")
	if isinstance(value, int):
		parsed = value
	elif isinstance(value, str):
		try:
			parsed = int(value.strip())
		except ValueError:
			raise ValueError(
				f"invalid QPM completion retention value for "
				f"{source}: {value!r}") from None
	else:
		raise ValueError(
			f"invalid QPM completion retention value for {source}: "
			f"{value!r}")
	if parsed <= 0:
		raise ValueError(
			f"QPM completion retention value for {source} must be "
			"positive")
	return parsed


def _completion_payload_failed(payload):
	if not isinstance(payload, dict):
		return False
	rc = payload.get("rc")
	if rc not in (None, 0):
		return True
	outcome = payload.get("outcome")
	return outcome is not None and str(outcome).upper() == "FAILED"


def _completion_record_size_bytes(record):
	public = {
		key: value
		for key, value in record.items()
		if not str(key).startswith("_qpm_")
	}
	return len(repr(public).encode("utf-8", errors="replace"))


def _completion_selectors(reservation_id=None, cid=None, qtask_id=None):
	selectors = []
	if cid is not None:
		selectors.append(("cid", cid))
	if qtask_id is not None:
		selectors.append(("qtask_id", qtask_id))
	if reservation_id is not None and not selectors:
		selectors.append(("reservation", reservation_id))
	return selectors


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


def _allowed_reservation_operations(request, operation):
	raw_operations = (
		request.get("allowed_operations") or
		request.get("operations"))
	if isinstance(raw_operations, str):
		operations = [raw_operations]
	elif isinstance(raw_operations, (list, tuple, set)):
		operations = list(raw_operations)
	else:
		operations = list(DEFAULT_RESERVATION_OPERATIONS)
	if operation is not None:
		operations.append(operation)

	normalized = []
	seen = set()
	for item in operations:
		if item is None:
			continue
		value = _normalized_operation_name(item)
		if value in seen:
			continue
		seen.add(value)
		normalized.append(value)
	return normalized


def _redacted_metadata(value):
	if isinstance(value, dict):
		redacted = {}
		for key, item in value.items():
			if _is_sensitive_metadata_key(key):
				redacted[key] = "<redacted>"
			else:
				redacted[key] = _redacted_metadata(item)
		return redacted
	if isinstance(value, list):
		return [_redacted_metadata(item) for item in value]
	if isinstance(value, tuple):
		return [_redacted_metadata(item) for item in value]
	return value


def _is_sensitive_metadata_key(key):
	name = str(key).strip().lower().replace("-", "_")
	if name == "secret_material":
		return False
	return any(part in name for part in SENSITIVE_METADATA_KEY_PARTS)


def _drop_none(value):
	if isinstance(value, dict):
		return {
			key: _drop_none(item)
			for key, item in value.items()
			if item is not None
		}
	if isinstance(value, list):
		return [_drop_none(item) for item in value if item is not None]
	if isinstance(value, tuple):
		return [_drop_none(item) for item in value if item is not None]
	return value


def _normalize_directory_event_type(event_type):
	if event_type is None:
		return ""
	return str(event_type).strip().lower().replace("_", "-")


def _int_or_none(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _estimate_total_ns(estimate):
	return _int_or_zero(
		estimate.get("total_ns", estimate.get("estimated_total_ns", 0)))


def _estimate_baseline_units(estimate):
	return _int_or_zero(estimate.get("baseline_units", 0))


def _int_or_zero(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return 0


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


def _metadata_identifier(metadata, direct_key, external_key):
	value = metadata.get(direct_key)
	if value is not None:
		return value
	return metadata.get(external_key)


def _metadata_identifier_any(metadata, *keys):
	for key in keys:
		value = metadata.get(key)
		if value is not None:
			return value
	return None


def _operation_from_metadata(metadata):
	for source in (
			metadata,
			metadata.get("run_context", {}),
			metadata.get("workload", {})):
		if not isinstance(source, dict):
			continue
		value = source.get("operation")
		if value is None:
			value = source.get("operation_type")
		if value is not None:
			return value
	return None


def _operation_names_compatible(expected, requested):
	expected = _normalized_operation_name(expected)
	requested = _normalized_operation_name(requested)
	if expected == requested:
		return True
	execution_operations = ("execution", "async_run", "sync_run")
	return expected == "execution" and requested in execution_operations


def _normalized_operation_name(value):
	return str(value).strip().lower().replace("-", "_")


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


def _nonnegative_config_int(value, field):
	if isinstance(value, bool):
		raise QPMAdmissionValidationError(f"{field} must be an integer")
	try:
		value = int(value)
	except (TypeError, ValueError) as error:
		raise QPMAdmissionValidationError(
			f"{field} must be an integer") from error
	if value < 0:
		raise QPMAdmissionValidationError(
			f"{field} must not be negative")
	return value


def _positive_config_int(value, field):
	value = _nonnegative_config_int(value, field)
	if value == 0:
		raise QPMAdmissionValidationError(
			f"{field} must be greater than zero")
	return value


def _token_metadata(token):
	if token is None:
		return {}
	return {
		"present": True,
		"type": type(token).__name__,
	}


def _request_metadata(reservation_metadata, context_metadata):
	metadata = dict(reservation_metadata or {})
	context_metadata = dict(context_metadata or {})
	for key, value in context_metadata.items():
		metadata.setdefault(key, value)
	if context_metadata:
		metadata["execution_context"] = context_metadata
	return metadata
