import atexit
import os
import re
import time
import logging
import threading
import yaml
import sys
from collections import deque
from importlib.metadata import PackageNotFoundError, version

from qiskit.providers import BackendV2, Options

from .qfw_job import (
	QFwJob, QFW_INFO_PASSTHROUGH_OPTIONS, SHIM_LIB_ENV,
	normalize_reservation_id, normalize_shim_library)
from .qfw_metadata import get_qubit_mapping, set_qubit_mapping
from .qfw_target import (
	QFW_NUM_QUBITS,
	build_qfw_target,
	qfw_basis_gates,
	target_from_telemetry,
)
from .qfw_telemetry import QFwTelemetry
from defw_exception import DEFwDumper
from defw import me
from .qfw_lookup_service import get_qpm, get_qpm_telemetry
from .qpm_selection import qpm_selection_for_provider
from api_qpm_common import QPMCapability
from defw_event_baseapi import BaseEventAPI
from defw_common_def import g_rpc_metrics
from util import instrumentation

QFW_RUN_CONTEXT_OPTIONS = (
	"reservation_id",
	"token",
	"timeout",
	"cancel_on_timeout",
)


class CircuitMetrics:
	def __init__(self, window_size=4096):
		self.lock = threading.Lock()
		self.window_size = window_size
		self.db = {}

	def add_timing_locked(self, send_time, recv_time, db):
		rtt = recv_time - send_time
		db['total'] += 1
		db['window'].append(rtt)
		window_len = len(db['window'])
		if window_len > 0:
			db['avg'] = sum(db['window']) / window_len
		if rtt > db['max']:
			db['max'] = rtt
		if rtt < db['min']:
			db['min'] = rtt

	def add_time(self, start_time, end_time, label):
		with self.lock:
			if label not in self.db:
				self.db[label] = {
					'window': deque(maxlen=self.window_size),
					'avg': 0.0, 'min': sys.maxsize, 'max': 0.0,
					'total': 0
				}
			self.add_timing_locked(start_time, end_time, self.db[label])

	def dump(self):
		import copy

		with self.lock:
			dbcopy = copy.deepcopy(self.db)
		for k, v in dbcopy.items():
			del v['window']
		logging.defw_app("QFw Backend statistics")
		logging.defw_app(yaml.dump(dbcopy, Dumper=DEFwDumper, indent=2, sort_keys=False))


g_circ_metrics = CircuitMetrics()

EVENT_TYPE_CIRC_RESULT = 1


def _mqt_core_version():
	try:
		raw = version('mqt-core')
	except PackageNotFoundError:
		return None, None
	match = re.match(r'(\d+(?:\.\d+)*)', raw)
	if match is None:
		return raw, None
	return raw, tuple(int(p) for p in match.group(1).split('.'))


class QFwBackend(BackendV2):
	BACKEND_NAME = "QFw Backend"
	BACKEND_VERSION = "1.0"
	COMPLETION_TIMEOUT_SEC = 200

	def __init__(self, betype=-1, capability=-1, target=None, properties=None,
				 num_qubits=QFW_NUM_QUBITS, lookup_timeout=None,
				 provider=None, backend=None, service_id=None):
		# Telemetry for the application process. Off unless QFW_TELEMETRY
		# says otherwise; see util.instrumentation.
		instrumentation.configure_process("client")
		self.log_time = time.time()
		self._provider = None
		selector = provider if provider is not None else backend
		if selector is not None:
			selection = qpm_selection_for_provider(selector)
			self._provider = selection["provider"]
			if betype in (-1, None):
				betype = selection["qpm_type"]
			if capability in (-1, None):
				capability = selection["qpm_capabilities"]
		self._capability = capability
		lookup_kwargs = {}
		if lookup_timeout is not None:
			lookup_kwargs["timeout"] = lookup_timeout
		if self._provider is not None:
			lookup_kwargs["provider"] = self._provider
		self.qpm, reservation_id = get_qpm(
			betype, capability,
			service_id=service_id,
			return_reservation=True,
			**lookup_kwargs)
		self._qpm_lifecycle_binding = getattr(
			self.qpm, "lifecycle_binding", None)
		# The QPM's telemetry API, bound next to the execution API on the
		# same managed binding. It describes the device behind self.target.
		self._telemetry_api = get_qpm_telemetry(self.qpm)
		self._telemetry = None
		self._target_source = None
		self.event_api = BaseEventAPI()
		self.event_api.register_external()
		self._event_endpoint = me.my_endpoint()
		self._event_registration_lock = threading.Lock()
		self._completion_event_registered = False
		self._completion_event_registration = None
		if self._qpm_lifecycle_binding is not None:
			self._qpm_lifecycle_binding.add_reconnect_listener(
				self._handle_qpm_reconnect)

		super().__init__(name=self.my_name())
		if reservation_id is not None:
			self.options.reservation_id = reservation_id
		self._target = target
		self._properties = properties
		self._num_qubits = num_qubits

		# Custom option values for config, properties
		self._options_configuration = {}
		self._options_properties = {}

		self.options.set_validator("shots", (1, 65536))
		self.options.set_validator("seed_simulator", int)
		self.options.set_validator("seed", int)
		self.register_completion_events()
		atexit.register(self._unregister_completion_events)

	def __copy__(self):
		return self

	def __deepcopy__(self, memo):
		return self

	def returns_statevector(self):
		if self._capability == -1:
			return False
		return bool(int(self._capability) & int(
			QPMCapability.QPM_CAP_STATEVECTOR))

	def set_qubit_mapping(self, circuit, mapping):
		return set_qubit_mapping(circuit, mapping)

	def get_qubit_mapping(self, circuit):
		return get_qubit_mapping(circuit)

	def shutdown(self):
		g_circ_metrics.dump()
		if self._qpm_lifecycle_binding is not None:
			self._qpm_lifecycle_binding.remove_reconnect_listener(
				self._handle_qpm_reconnect)
		self._unregister_completion_events()
		if self._qpm_lifecycle_binding is not None:
			self._qpm_lifecycle_binding.close()
		me.exit()

	def _unregister_completion_events(self):
		with self._event_registration_lock:
			registration = self._completion_event_registration
			self._completion_event_registered = False
			self._completion_event_registration = None
		self._unregister_registration(registration)

	def _unregister_registration(self, registration):
		registration_id = (
			registration.get("registration_id")
			if isinstance(registration, dict) else None)
		if registration_id is None:
			return
		try:
			self.qpm.unregister_event_notification(registration_id)
		except Exception:
			pass

	def configuration(self):
		# LEGACY (BackendV1): Return configuration dict for backward compatibility
		# BackendV2 doesn't require this method but some legacy code may use it
		target = self.target
		coupling_map = target.build_coupling_map()
		configuration_dict = {
			"backend_name": self.my_name(),
			"backend_version": self.my_version(),
			"n_qubits": target.num_qubits,
			"basis_gates": sorted(target.operation_names) or qfw_basis_gates(),
			"coupling_map": (
				None if coupling_map is None else
				[list(edge) for edge in coupling_map.get_edges()]),
			"simulator": True,
			"local": True,
			"conditional": True,
			'open_pulse': False,
			"memory": True,
			"max_shots": 65536,
			"description": "Backend for using the quantum framework on frontier with qiskit!",
			"gates": []
		}

		return configuration_dict

	def properties(self):
		# LEGACY (BackendV1): Return properties dict for backward compatibility
		return self._options_properties

	@property
	def telemetry(self):
		"""The QPM's telemetry API (QFwTelemetry), or None when the QPM
		connection carries no managed binding to select it from."""
		if self._telemetry is None and self._telemetry_api is not None:
			self._telemetry = QFwTelemetry(
				self._telemetry_api, context=self._telemetry_context)
		return self._telemetry

	def _telemetry_context(self):
		# What every telemetry call carries: the shim library a run would
		# use, so the device described is the one the circuits reach, and
		# the reservation context when the run context holds one.
		properties = getattr(self.qpm, "qpm_properties", None) or {}
		context = {"lib": (self._shim_library() or
				   properties.get("description_library"))}

		reservation_id = getattr(self.options, "reservation_id", None)
		if reservation_id is not None:
			context["reservation_id"] = reservation_id

		token = getattr(self.options, "token", None)
		if token is not None:
			context["token"] = token
		return context

	@property
	def target(self):
		"""The device target: what the QPM's telemetry describes, built once
		per backend, else QFw's generic target (see target_source)."""
		if self._target is None:
			self._target = self._build_target()
		return self._target

	@property
	def target_source(self):
		"""Where self.target came from: "device: <provider> <device>" or
		"generic: <reason>"; None until the target has been built."""
		return self._target_source

	def _build_target(self):
		generic = f"the generic {self._num_qubits}-qubit QFw target"
		telemetry = self.telemetry
		if telemetry is None:
			reason = "the QPM connection has no telemetry binding"
			logging.info(f"QFw backend: {reason}; using {generic}")
			self._target_source = f"generic: {reason}"
			return build_qfw_target(self._num_qubits)
		try:
			info = telemetry.get_backend_info() or {}
			target = target_from_telemetry(telemetry, backend_info=info)
		except Exception as exc:
			reason = f"the QPM's device telemetry failed: {exc}"
			logging.warning(f"QFw backend: {reason}; using {generic}")
			self._target_source = f"generic: {reason}"
			return build_qfw_target(self._num_qubits)
		if target is None:
			name = info.get("backend") or self._provider or "default"
			reason = f"QPM {name} reports no device metadata"
			logging.info(f"QFw backend: {reason}; using {generic}")
			self._target_source = f"generic: {reason}"
			return build_qfw_target(self._num_qubits)
		self._target_source = f"device: {target.description}"
		logging.info(f"QFw backend: {target.description}: "
			     f"{target.num_qubits} qubits, "
			     f"{len(target.operation_names)} operations")
		return target

	def _shim_library(self, lib=None):
		# The shim library (qrmi or qdmi) a run goes through: the run()
		# argument, else the backend option, else QFW_SHIM_LIB, else the
		# shim's own default (None).
		if lib is None:
			lib = self.options.lib
		if lib is None:
			lib = os.environ.get(SHIM_LIB_ENV)
		return normalize_shim_library(
			lib, getattr(self.qpm, "qpm_properties", None))

	@property
	def max_circuits(self):
		return 1024

	@classmethod
	def _default_options(cls):
		return Options(
			shots=1024,
			seed=334,
			seed_simulator=334,
			reservation_id=None,
			token=None,
			timeout=None,
			cancel_on_timeout=False,
			lib=None,
			isa_policy=None,
		)

	def compile(self, circuits, compiler='qiskit', **kwargs):
		if compiler == 'qiskit':
			from qiskit import transpile
			return transpile(circuits, target=self.target, **kwargs)
		elif compiler == 'mqt':
			from mqt.core import mlir

			# Version check the installed MQTC.
			# TODO: Once setup/qdmi-requirements.txt pins a newer MQT
			# Core, remove this check and the NOTE in examples/README.md.
			require_mqtc_above = (4, 1, 1)
			mqtc_ver_str, mqtc_ver = _mqt_core_version()
			if mqtc_ver is not None and mqtc_ver <= require_mqtc_above:
				require_mqtc_str = '.'.join(str(p) for p in require_mqtc_above)
				raise RuntimeError(
					f"The 'mqt' compiler requires an MQT Core newer than "
					f"{require_mqtc_str}, but {mqtc_ver_str} is installed. "
					"See notes for qfw_qiskit_simple_w_mqt.sh in "
					"examples/README.md for more information.")
			return mlir.compile_program(
				circuits, target=self.target, **kwargs)
		else:
			raise ValueError(
				f"unknown compiler '{compiler}'; expected 'qiskit' or 'mqt'")

	def run(self, circuits, **kwargs):
		for kwarg in kwargs:
			if (not hasattr(self.options, kwarg) and
				kwarg not in QFW_RUN_CONTEXT_OPTIONS and
				kwarg not in QFW_INFO_PASSTHROUGH_OPTIONS):
				print("Option ", kwarg, " is not used by this backend")
		options = {
			"seed_simulator": kwargs.get("seed_simulator", self.options.seed_simulator),
			"shots": kwargs.get("shots", self.options.shots),
			"seed": kwargs.get("seed", self.options.seed),
		}
		for key in QFW_RUN_CONTEXT_OPTIONS:
			if key in kwargs:
				options[key] = kwargs[key]
				continue
			value = getattr(self.options, key, None)
			if value is not None and (
					key != "cancel_on_timeout" or value is not False):
				options[key] = value
		if "reservation_id" in options:
			options["reservation_id"] = normalize_reservation_id(
				options["reservation_id"])
		lib = self._shim_library(kwargs.get("lib"))
		if lib is not None:
			options["lib"] = lib
		isa_policy = kwargs.get("isa_policy", self.options.isa_policy)
		if isa_policy is not None:
			options["isa_policy"] = isa_policy
		for key in QFW_INFO_PASSTHROUGH_OPTIONS:
			if key in kwargs:
				options[key] = kwargs[key]
		self._qfw_job = QFwJob(self, self.qpm, self.event_api, circuits, options)
		self._qfw_job.submit()
		return self._qfw_job

	def register_completion_events(self):
		with self._event_registration_lock:
			if self._completion_event_registered:
				return self._completion_event_registration
			self._completion_event_registration = \
				self.qpm.register_event_notification(
					self._event_endpoint,
					EVENT_TYPE_CIRC_RESULT,
					self.event_api.class_id())
			self._completion_event_registered = True
			return self._completion_event_registration

	def _handle_qpm_reconnect(self, state):
		with self._event_registration_lock:
			previous = self._completion_event_registration
			self._completion_event_registered = False
			self._completion_event_registration = None
		if state.get("same_runtime"):
			self._unregister_registration(previous)
			self.register_completion_events()

	def log_statistics(self, res):
		g_circ_metrics.add_time(res['creation_time'], res['launch_time'], "creation->launch")
		g_circ_metrics.add_time(res['resources_consumed_time'], res['exec_time'], "resources->exec")
		g_circ_metrics.add_time(res['exec_time'], res['completion_time'], "exec->completion")
		g_circ_metrics.add_time(res['cq_enqueue_time'], res['cq_dequeue_time'], "enqueue->dequeue")

	def dump_statistics(self):
		if time.time() - self.log_time > 30:
			g_circ_metrics.dump()
			g_rpc_metrics.dump()
			self.log_time = time.time()

	def my_name(self):
		return self.BACKEND_NAME

	def my_version(self):
		return self.BACKEND_VERSION
