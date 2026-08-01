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
	scheduler_context_available,
	scheduler_task_count,
	set_scheduler_policy as activate_scheduler_policy,
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
QPM_TASK_SUBMITTED = "submitted"
QPM_TASK_COMPLETED = "completed"
QPM_TASK_FAILED = "failed"
QPM_TASK_CANCELLED = "cancelled"


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
			return self._scheduler_control_result("draining", version)

	def set_dispatch_depth(self, max_inflight):
		with self.lock:
			depth = int(max_inflight)
			if depth < 1:
				raise ValueError("dispatch depth must be at least 1")
			self.scheduler_control["dispatch_depth"] = depth
			version = self._bump_scheduler_config_version(
				"scheduler_control")
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
				"runtime_tasks": [
					self._task_status_locked(runtime)
					for runtime in self.runtime_by_qtask_id.values()
				],
			}

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
			self._require_reservation_matches_context(
				reservation, request_context)
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
			runtime = QPMRuntimeTask(
				cid=cid,
				qtask_id=qtask_id,
				reservation_id=request_context.reservation_id,
				token_metadata=_token_metadata(request_context.token),
				owner_metadata=dict(request_context.owner),
				request_metadata={
					"target_device_id": request_context.target_device_id,
					"scope_id": request_context.scope_id,
					"workload": dict(request_context.workload),
					"policy": dict(request_context.policy),
					"run_context": dict(request_context.run_context),
				},
			)
			runtime.external_ids, runtime.canonical_ids = (
				self.canonicalize_request_context(request_context))
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

	def canonicalize_request_context(self, request_context):
		external_ids = {}
		canonical_ids = {}
		owner_id = _owner_identifier(request_context.owner)
		for kind, value in (
			("owner_id", owner_id),
			("job_id", request_context.job_id),
			("allocation_id", request_context.allocation_id),
			("project_id", request_context.project_id),
			("session_id", request_context.session_id),
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
			runtime.state = QPM_TASK_COMPLETED
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
			"owner_metadata": dict(request_context.owner),
			"auth_disabled": request_context.auth_disabled,
			"timestamp": time.time(),
		}
		with self.lock:
			self.audit_records.append(record)
			self.diagnostic_bypass_records.append(record)
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

	def _task_status_locked(self, runtime):
		return {
			"cid": runtime.cid,
			"qtask_id": runtime.qtask_id,
			"reservation_id": runtime.reservation_id,
			"scheduler_task_id": runtime.scheduler_task_id,
			"provider_handle": runtime.provider_handle,
			"state": runtime.state,
		}

	def _terminal_task_for_selector_locked(self, cid=None, qtask_id=None):
		if qtask_id is not None:
			return self.terminal_tasks_by_qtask_id.get(qtask_id)
		if cid is not None:
			return self.terminal_tasks_by_cid.get(cid)
		return None

	def _event_registration_matches_locked(self, registration, evtype,
					       payload):
		if registration.get("evtype") != evtype:
			return False
		runtime = None
		if isinstance(payload, dict):
			qtask_id = payload.get("qtask_id")
			cid = payload.get("cid")
			if qtask_id is not None:
				runtime = self.runtime_by_qtask_id.get(qtask_id)
			if runtime is None and cid is not None:
				runtime = self.runtime_by_cid.get(cid)
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

	def _require_reservation_matches_context(self, reservation,
						 request_context):
		metadata = self._reservation_metadata_locked(reservation)
		self._compare_reservation_field(
			reservation,
			"device_id",
			_numeric_or_canonical(
				self, "device_id", None,
				request_context.target_device_id)
			if request_context.target_device_id is not None else None)
		self._compare_reservation_field(
			reservation,
			"scope_id",
			_numeric_or_canonical(
				self, "scope_id", None, request_context.scope_id)
			if request_context.scope_id is not None else None)
		self._compare_reservation_field(
			reservation,
			"job_id",
			_numeric_or_canonical(
				self, "job_id", None, request_context.job_id)
			if request_context.job_id is not None else None)
		self._compare_reservation_metadata(
			metadata, "external_device_id",
			request_context.target_device_id, "target_device_id")
		self._compare_reservation_metadata(
			metadata, "external_scope_id",
			request_context.scope_id, "scope_id")
		self._compare_reservation_metadata(
			metadata, "external_job_id",
			self._request_job_identifier(request_context), "job_id")
		self._compare_reservation_metadata(
			metadata, "external_allocation_id",
			request_context.allocation_id, "allocation_id")
		self._compare_reservation_metadata(
			metadata, "external_project_id",
			request_context.project_id, "project_id")
		self._compare_reservation_metadata(
			metadata, "external_session_id",
			request_context.session_id, "session_id")
		self._compare_reservation_metadata(
			metadata, "policy", request_context.policy, "policy")
		self._compare_reservation_metadata(
			metadata, "workload", request_context.workload, "workload")
		self._compare_reservation_metadata(
			metadata, "run_context", request_context.run_context,
			"run_context")
		self._compare_reservation_metadata(
			metadata, "operation",
			self._request_operation(request_context), "operation")

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

	def _request_job_identifier(self, request_context):
		if request_context.job_id is not None:
			return request_context.job_id
		return request_context.allocation_id

	def _request_operation(self, request_context):
		for source in (request_context.run_context, request_context.workload):
			operation = source.get("operation")
			if operation is not None:
				return operation
		return "execution"

	def _compare_reservation_field(self, reservation, field, expected):
		if expected is None:
			return
		actual = reservation.get(field)
		if actual in (None, expected):
			return
		raise QPMAdmissionValidationError(
			f"reservation {field} mismatch: expected={expected} "
			f"actual={actual}")

	def _compare_reservation_metadata(self, metadata, field, expected, label):
		if expected is None:
			return
		if isinstance(expected, dict) and not expected:
			return
		if field not in metadata:
			return
		actual = metadata.get(field)
		if actual is None:
			return
		if actual == expected:
			return
		raise QPMAdmissionValidationError(
			f"reservation {label} mismatch: expected={expected} "
			f"actual={actual}")
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
