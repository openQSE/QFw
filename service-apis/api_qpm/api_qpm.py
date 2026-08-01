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


class QPMRemoteBase(BaseRemote):
	def __init__(self, si):
		super().__init__(service_info=si)


class QPMExecution(QPMRemoteBase):
	def delete_circuit(self, cid):
		pass

	def sync_run(self, info, reservation_id=None, token=None,
				 run_context=None, timeout=None, cancel_on_timeout=False,
				 **request_metadata):
		pass

	def async_run(self, info, reservation_id=None, token=None,
				  run_context=None, timeout=None, cancel_on_timeout=False,
				  **request_metadata):
		pass

	def diagnostic_sync_run(self, info, token=None, reason=None,
				**request_metadata):
		pass

	def diagnostic_async_run(self, info, token=None, reason=None,
				 **request_metadata):
		pass

	def is_ready(self):
		pass

	def read_cq(self, cid=None):
		pass

	def peek_cq(self, cid=None):
		pass

	def register_event_notification(self, ep, evtype, class_id):
		pass


class QPMAdmissionControl(QPMRemoteBase):
	def evaluate(self, request, token=None):
		pass

	def reserve(self, request, token=None):
		pass

	def renew(self, reservation_id, request=None, token=None):
		pass

	def release(self, reservation_id, token=None):
		pass

	def cancel(self, reservation_id, reason=None, token=None):
		pass

	def get_reservation(self, reservation_id, token=None):
		pass

	def list_reservations(self, filters=None, token=None):
		pass


class QPMAdmissionPolicyConfig(QPMRemoteBase):
	def get_admission_policy(self, token=None):
		pass

	def set_admission_policy(self, policy, token=None):
		pass

	def get_capacity_model(self, token=None):
		pass

	def set_capacity_model(self, capacity_model, token=None):
		pass


class QPMSchedulerControl(QPMRemoteBase):
	def get_scheduler_status(self, token=None):
		pass

	def set_scheduler_policy(self, policy, token=None):
		pass

	def pause(self, target_id=None, token=None):
		pass

	def resume(self, target_id=None, token=None):
		pass

	def drain(self, target_id=None, token=None):
		pass


class QPMTelemetry(QPMRemoteBase):
	def get_backend_info(self, lib=None, token=None):
		pass

	def get_device_info(self, lib=None, token=None):
		pass

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		pass

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		pass

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		pass

	def get_last_job_timing(self, cid=None, lib=None, token=None):
		pass

	def get_last_job_metadata(self, cid=None, lib=None, token=None):
		pass

	def test(self):
		pass

	def shutdown(self):
		pass


class QPM(QPMExecution, QPMAdmissionControl, QPMAdmissionPolicyConfig,
	  QPMSchedulerControl, QPMTelemetry):
	pass
