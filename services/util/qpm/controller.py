import os
import threading
from dataclasses import dataclass


TARGET_ID_ENV = "QFW_QPM_TARGET_ID"
DEVICE_ID_ENV = "QFW_QPU_DEVICE_ID"
ADMISSION_THREADING_ENV = "QFW_QPM_ADMISSION_THREADING_MODE"
SCHEDULER_THREADING_ENV = "QFW_QPM_SCHEDULER_THREADING_MODE"
CONTROLLER_SERIALIZATION_ENV = "QFW_QPM_CONTROLLER_SERIALIZATION_MODE"

DEFAULT_ADMISSION_THREADING_MODE = "QHW_ADM_THREAD_SAFE"
DEFAULT_SCHEDULER_THREADING_MODE = "QHW_SCHED_THREAD_SAFE"
DEFAULT_CONTROLLER_SERIALIZATION_MODE = "controller-lock"


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
			})
		return info


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
