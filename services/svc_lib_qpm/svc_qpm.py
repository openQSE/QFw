import logging
import os
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM
from util.qpm.util_circuit import set_max_qubits_pp

MAX_SHIM_QUBITS = 1024
MAX_SHIM_SHOTS = 10000


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		super().__init__(QRC(start=start), max_ppn=1, start=start)
		set_max_qubits_pp(MAX_SHIM_QUBITS)
		self.configure_device_profile(
			max_qubits=MAX_SHIM_QUBITS,
			max_shots=MAX_SHIM_SHOTS,
			time_span_ns=60_000_000_000)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm import QPMType, QPMCapability
		properties = dict(svc_info.get('properties', {}))
		device_id = os.environ.get('QFW_QPU_DEVICE_ID')
		if device_id:
			properties['device_id'] = device_id
		info = self.query_helper(
			QPMType.QPM_TYPE_HARDWARE,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			SERVICE_NAME, SERVICE_DESC,
			properties=properties)
		logging.debug(f"shim {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		# Hardware is still the IQM q20 (reached via QRMI / QDMI-on-IQM), so the
		# qhw backend tag stays 'iqm' until per-library normalizers land.
		info['qfw_backend'] = 'iqm'
		return info

	def capability_map(self):
		return self.qrc.capability_map()

	def get_backend_info(self, lib=None, token=None):
		return self.qrc.get_backend_info(lib=lib)

	def get_device_info(self, lib=None, token=None):
		return self.qrc.get_device_info(lib=lib)

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		return self.qrc.get_dynamic_backend_info(calibration_set_id, lib=lib)

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return self.qrc.get_calibration_snapshot(calibration_set_id, lib=lib)

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		return self.qrc.get_coupling_graph(calibration_set_id, lib=lib)

	def test(self):
		return "****Shim (QRMI/QDMI) QPM Test Successful****"
