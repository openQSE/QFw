import time
import logging
import threading
import yaml
import sys
from collections import deque

from qiskit.providers import BackendV2, Options

from .qfw_job import QFwJob
from .qfw_target import QFW_NUM_QUBITS, build_qfw_target, qfw_basis_gates
from defw_exception import DEFwDumper
from defw import me
from .qfw_lookup_service import get_qpm
from enum import IntFlag
from api_qpm import QPMType, QPMCapability
from defw_event_baseapi import BaseEventAPI
from defw_common_def import g_rpc_metrics

# This is a mirror of QPMType and QPMCapability. And they always need to
# match. The point here is not to expose QPM specific information to the
# application as they should always be abstracted away by the backend.
_qfw_type_names = {name.replace("QPM_", "QFW_"): m for name, m in QPMType.__members__.items()}
QFwBackendType = IntFlag('QFwBackendType', _qfw_type_names)
_qfw_cap_names = {name.replace("QPM_", "QFW_"): m for name, m in QPMCapability.__members__.items()}
QFwBackendCapability = IntFlag('QFwBackendCapability', _qfw_cap_names)


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

		dbcopy = copy.deepcopy(self.db)
		for k, v in dbcopy.items():
			del v['window']
		logging.defw_app("QFw Backend statistics")
		logging.defw_app(yaml.dump(dbcopy, Dumper=DEFwDumper, indent=2, sort_keys=False))


g_circ_metrics = CircuitMetrics()

EVENT_TYPE_CIRC_RESULT = 1


class QFwBackend(BackendV2):
	BACKEND_NAME = "QFw Backend"
	BACKEND_VERSION = "1.0"
	COMPLETION_TIMEOUT_SEC = 200

	def __init__(self, betype=-1, capability=-1, target=None, properties=None,
				 num_qubits=QFW_NUM_QUBITS):
		self.log_time = time.time()
		self.qpm = get_qpm(betype, capability)
		# register for events with the qpm
		self.event_api = BaseEventAPI()
		self.event_api.register_external()
		self.qpm.register_event_notification(
			me.my_endpoint(), EVENT_TYPE_CIRC_RESULT, self.event_api.class_id())

		super().__init__(name=self.my_name())
		self._target = target
		self._properties = properties
		self._num_qubits = num_qubits

		# Custom option values for config, properties
		self._options_configuration = {}
		self._options_properties = {}

		self.options.set_validator("shots", (1, 65536))
		self.options.set_validator("seed_simulator", int)
		self.options.set_validator("seed", int)

	def __copy__(self):
		return self

	def __deepcopy__(self, memo):
		return self

	# This is unique to the QFw backend. We need to cleanly shutdown the
	# QFw infrastructure.
	def shutdown(self):
		g_circ_metrics.dump()
		self.qpm.shutdown()
		me.exit()

	def configuration(self):
		# LEGACY (BackendV1): Return configuration dict for backward compatibility
		# BackendV2 doesn't require this method but some legacy code may use it
		configuration_dict = {
			"backend_name": self.my_name(),
			"backend_version": self.my_version(),
			"n_qubits": self._num_qubits,
			"basis_gates": qfw_basis_gates(),
			"coupling_map": None,
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
	def target(self):
		if self._target is not None:
			return self._target

		self._target = build_qfw_target(self._num_qubits)
		return self._target

	@property
	def max_circuits(self):
		return 1024

	@classmethod
	def _default_options(cls):
		return Options(shots=1024, seed=334, seed_simulator=334)

	def run(self, circuits, **kwargs):
		for kwarg in kwargs:
			if not hasattr(self.options, kwarg):
				print("Option ", kwarg, " is not used by this backend")
		options = {
			"seed_simulator": kwargs.get("seed_simulator", self.options.seed_simulator),
			"shots": kwargs.get("shots", self.options.shots),
			"seed": kwargs.get("seed", self.options.seed),
		}
		self._qfw_job = QFwJob(self, self.qpm, self.event_api, circuits, options)
		self._qfw_job.submit()
		return self._qfw_job

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
