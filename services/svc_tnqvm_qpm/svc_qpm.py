import logging
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM

MAX_TNQVM_QUBITS = 32
MAX_TNQVM_SHOTS = 1_000_000


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		super().__init__(QRC(start=start), start=start)
		self.configure_device_profile(
			max_qubits=MAX_TNQVM_QUBITS,
			max_shots=MAX_TNQVM_SHOTS)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC
		from api_qpm_common import QPMType, QPMCapability
		info = self.query_helper(
			QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_TENSORNETWORK,
			SERVICE_NAME, SERVICE_DESC,
			properties={
				"provider": "tnqvm",
				"num_qubits": MAX_TNQVM_QUBITS,
				"max_shots": MAX_TNQVM_SHOTS,
			})
		logging.debug(f"TNQVM {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		info['qfw_backend'] = 'circuit_runner.tnqvm'
		return info

	def get_backend_info(self, lib=None, token=None):
		return {
			'backend': 'tnqvm',
			'metadata_supported': False,
		}

	def get_device_info(self, lib=None, token=None):
		return {
			'backend': 'tnqvm',
			'metadata_supported': False,
		}

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			'backend': 'tnqvm',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			'backend': 'tnqvm',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		return {
			'backend': 'tnqvm',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}
