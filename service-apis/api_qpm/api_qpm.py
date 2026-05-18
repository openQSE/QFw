from defw_remote import BaseRemote
from enum import IntFlag

VERSION = 0.1


class QPMType(IntFlag):
	QPM_TYPE_HARDWARE = 1 << 0
	QPM_TYPE_SIMULATOR = 1 << 1
	QPM_TYPE_QB = 1 << 2
	QPM_TYPE_TNQVM = 1 << 3
	QPM_TYPE_NWQSIM = 1 << 4
	QPM_TYPE_IQM = 1 << 5


class QPMCapability(IntFlag):
	QPM_CAP_TENSORNETWORK = 1 << 0
	QPM_CAP_STATEVECTOR = 1 << 1
	QPM_CAP_SUPERCONDUCTING = 1 << 2


class QPM(BaseRemote):
	def __init__(self, si):
		super().__init__(service_info=si)

	def delete_circuit(self, cid):
		pass

	def sync_run(self, info):
		pass

	def async_run(self, info):
		pass

	def is_ready(self):
		pass

	def read_cq(self, cid=None):
		pass

	def peek_cq(self, cid=None):
		pass

	def register_event_notification(self, ep, evtype, class_id):
		pass

	def get_backend_info(self):
		pass

	def get_device_info(self):
		pass

	def get_dynamic_backend_info(self, calibration_set_id=None):
		pass

	def get_calibration_snapshot(self, calibration_set_id=None):
		pass

	def get_coupling_graph(self, calibration_set_id=None):
		pass

	def get_last_job_timing(self, cid=None):
		pass

	def get_last_job_metadata(self, cid=None):
		pass

	def test(self):
		pass

	def shutdown(self):
		pass
