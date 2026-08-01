import os
import threading
from dataclasses import dataclass, field


TARGET_ID_ENV = "QFW_QPM_TARGET_ID"
DEVICE_ID_ENV = "QFW_QPU_DEVICE_ID"
ADMISSION_THREADING_ENV = "QFW_QPM_ADMISSION_THREADING_MODE"
SCHEDULER_THREADING_ENV = "QFW_QPM_SCHEDULER_THREADING_MODE"
CONTROLLER_SERIALIZATION_ENV = "QFW_QPM_CONTROLLER_SERIALIZATION_MODE"

DEFAULT_ADMISSION_THREADING_MODE = "QHW_ADM_THREAD_SAFE"
DEFAULT_SCHEDULER_THREADING_MODE = "QHW_SCHED_THREAD_SAFE"
DEFAULT_CONTROLLER_SERIALIZATION_MODE = "controller-lock"
QPM_TASK_CREATED = "created"


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
	def __init__(self, config):
		self.config = config
		self.lock = threading.RLock()
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
		self.timeout_state = {}
		self.result_state = {}
		self.external_id_maps = {}
		self.external_id_next = 1

	def bind(self, max_ppn):
		with self.lock:
			self.binding_count += 1
			if self.max_ppn is None:
				self.max_ppn = max_ppn
		return self

	def telemetry(self):
		info = self.config.telemetry()
		with self.lock:
			info.update({
				"binding_count": self.binding_count,
				"max_ppn": self.max_ppn,
				"resource_hosts": sorted(self.free_hosts.keys()),
				"runtime_task_count": len(self.runtime_by_qtask_id),
			})
		return info

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


def get_target_controller(config, max_ppn):
	with _CONTROLLERS_LOCK:
		controller = _CONTROLLERS.get(config.target_id)
		if controller is None:
			controller = QPMTargetController(config)
			_CONTROLLERS[config.target_id] = controller
		return controller.bind(max_ppn)


def clear_target_controllers():
	with _CONTROLLERS_LOCK:
		_CONTROLLERS.clear()


def _target_id(qrc, explicit_target_id):
	if explicit_target_id:
		return str(explicit_target_id)

	for env_name in (TARGET_ID_ENV, DEVICE_ID_ENV):
		value = os.environ.get(env_name)
		if value:
			return value

	qrc_type = type(qrc)
	return f"{qrc_type.__module__}.{qrc_type.__name__}"


def _owner_identifier(owner):
	for key in ("user_id", "user", "username", "account"):
		value = owner.get(key)
		if value is not None:
			return value
	return None


def _token_metadata(token):
	if token is None:
		return {}
	return {
		"present": True,
		"type": type(token).__name__,
	}
