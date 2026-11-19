import logging
import os

from defw_exception import DEFwExecutionError
from .device import ibm_device_records
from .settings import aer_settings_from_service_entry
from .svc_qrc import QRC
from util.mpi import this_service_entry
from util.qpm.util_circuit import set_max_qubits_pp
from util.qpm.util_qpm import UTIL_QPM

MAX_AER_SHOTS = 1_000_000
DEVICE_ENV = "QFW_QPU_DEVICE_ID"
SERVICE_ID_ENV = "QFW_QPM_SERVICE_ID"


def check_aer_options(options):
	from qiskit_aer import AerSimulator
	try:
		AerSimulator(**options)
	except Exception as error:
		raise DEFwExecutionError(f"aer refuses its settings: {error}")


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		self.settings = aer_settings_from_service_entry(this_service_entry())
		self.device = os.environ.get(DEVICE_ENV, "").strip() or None
		if self.settings.mode == "noisy" and not self.device:
			raise DEFwExecutionError(
				"aer mode noisy needs a device-id to emulate")
		check_aer_options(self.settings.options)
		self.configuration = self.properties = None
		if self.device:
			self.configuration, self.properties = ibm_device_records(
				self.device)
			self.num_qubits = int(self.configuration["n_qubits"])
		elif self.settings.num_qubits:
			self.num_qubits = self.settings.num_qubits
		else:
			raise DEFwExecutionError(
				"aer needs a device-id or num-qubits for its width")
		super().__init__(QRC(start=start), start=start)
		set_max_qubits_pp(self.num_qubits)
		self.configure_device_profile(
			max_qubits=self.num_qubits,
			max_shots=MAX_AER_SHOTS)

	@property
	def backend_name(self):
		return os.environ.get(SERVICE_ID_ENV) or self.device or "aer"

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm_common import QPMType, QPMCapability
		properties = dict(svc_info.get('properties', {}))
		properties.update({
			"num_qubits": self.num_qubits,
			"max_shots": MAX_AER_SHOTS,
			"aer_mode": self.settings.mode,
			**self.settings.published,
		})
		if self.device:
			properties["device_id"] = self.device
		info = self.query_helper(
			QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			SERVICE_NAME, SERVICE_DESC,
			properties=properties)
		logging.debug(f"Aer {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		info['qfw_backend'] = 'aer'
		info['aer'] = {
			"mode": self.settings.mode,
			"device_id": self.device,
			"options": self.settings.options,
		}
		return info

	def get_backend_info(self, lib=None, token=None, reservation_id=None):
		if not self.device:
			return {'backend': self.backend_name, 'metadata_supported': False}
		return {
			'backend': self.backend_name,
			'metadata_supported': True,
			'static_architecture': self.configuration,
			'backend_properties': self.properties,
		}

	def get_device_info(self, lib=None, token=None):
		return {
			'backend': self.backend_name,
			'num_qubits': self.num_qubits,
			'max_shots': MAX_AER_SHOTS,
			'metadata_supported': bool(self.device),
		}

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		return self.get_calibration_snapshot(calibration_set_id, lib, token)

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			'backend': self.backend_name,
			'calibration_set_id': calibration_set_id,
			'backend_properties': self.properties,
			'metadata_supported': bool(self.device),
		}

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		return {
			'backend': self.backend_name,
			'calibration_set_id': calibration_set_id,
			'couplings': (self.configuration or {}).get('coupling_map') or [],
			'metadata_supported': bool(self.device),
		}
