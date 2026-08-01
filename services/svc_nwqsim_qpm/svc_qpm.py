import logging
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM
from util.qpm.util_circuit import set_max_qubits_pp

MAX_NWQSIM_QUBITS = 32


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		super().__init__(QRC(start=start), start=start)
		set_max_qubits_pp(MAX_NWQSIM_QUBITS)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC
		from api_qpm import QPMType, QPMCapability
		info = self.query_helper(
			QPMType.QPM_TYPE_NWQSIM | QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_STATEVECTOR,
			SERVICE_NAME, SERVICE_DESC)
		logging.debug(f"NWQSIM {SERVICE_DESC}: {info}")
		return info

	def create_circuit(self, info):
		info['qfw_backend'] = 'circuit_runner.nwqsim'
		return super().create_circuit(info)

	def get_backend_info(self, token=None):
		return {
			'backend': 'nwqsim',
			'metadata_supported': False,
		}

	def get_device_info(self, token=None):
		return {
			'backend': 'nwqsim',
			'metadata_supported': False,
		}

	def get_dynamic_backend_info(self, calibration_set_id=None, token=None):
		return {
			'backend': 'nwqsim',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_calibration_snapshot(self, calibration_set_id=None, token=None):
		return {
			'backend': 'nwqsim',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_coupling_graph(self, calibration_set_id=None, token=None):
		return {
			'backend': 'nwqsim',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_last_job_timing(self, cid=None, token=None):
		return {
			'backend': 'nwqsim',
			'cid': cid,
			'metadata_supported': False,
		}

	def get_last_job_metadata(self, cid=None, token=None):
		return {
			'backend': 'nwqsim',
			'cid': cid,
			'metadata_supported': False,
		}

	def test(self):
		return "****NWQSIM QPM Test Successful****"
