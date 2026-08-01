import logging
import os
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM
from util.qpm.util_circuit import set_max_qubits_pp

MAX_IQM_QUBITS = 1024


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		super().__init__(QRC(start=start), max_ppn=1, start=start)
		set_max_qubits_pp(MAX_IQM_QUBITS)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm import QPMType, QPMCapability
		properties = dict(svc_info.get('properties', {}))
		device_id = os.environ.get('QFW_QPU_DEVICE_ID')
		if device_id:
			properties['device_id'] = device_id
		info = self.query_helper(
			QPMType.QPM_TYPE_IQM | QPMType.QPM_TYPE_HARDWARE,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			SERVICE_NAME, SERVICE_DESC,
			properties=properties)
		logging.debug(f"IQM {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		info['qfw_backend'] = 'iqm'
		return info

	def get_backend_info(self, token=None):
		return self.qrc.get_backend_info()

	def get_device_info(self, token=None):
		return self.qrc.get_device_info()

	def get_dynamic_backend_info(self, calibration_set_id=None, token=None):
		return self.qrc.get_dynamic_backend_info(calibration_set_id)

	def get_calibration_snapshot(self, calibration_set_id=None, token=None):
		return self.qrc.get_calibration_snapshot(calibration_set_id)

	def get_coupling_graph(self, calibration_set_id=None, token=None):
		return self.qrc.get_coupling_graph(calibration_set_id)

	def get_last_job_timing(self, cid=None, token=None):
		return self.qrc.get_last_job_timing(cid)

	def get_last_job_metadata(self, cid=None, token=None):
		return self.qrc.get_last_job_metadata(cid)

	def test(self):
		return "****IQM QPM Test Successful****"
