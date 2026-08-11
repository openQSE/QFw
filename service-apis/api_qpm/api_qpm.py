from defw_remote import BaseRemote
from enum import IntFlag

VERSION = 0.1


class QPMType(IntFlag):
	QPM_TYPE_HARDWARE = 1 << 0
	QPM_TYPE_SIMULATOR = 1 << 1


class QPMCapability(IntFlag):
	QPM_CAP_TENSORNETWORK = 1 << 0
	QPM_CAP_STATEVECTOR = 1 << 1
	QPM_CAP_SUPERCONDUCTING = 1 << 2


class QPMRemoteBase(BaseRemote):
	def __init__(self, si):
		super().__init__(service_info=si)


class QPMExecution(QPMRemoteBase):
	def delete_circuit(self, cid, reservation_id=None, token=None):
		pass

	def sync_run(self, info, reservation_id=None, token=None, timeout=None,
				 cancel_on_timeout=False):
		pass

	def async_run(self, info, reservation_id=None, token=None, timeout=None,
				  cancel_on_timeout=False):
		pass

	def is_ready(self):
		pass

	def read_cq(self, cid=None, reservation_id=None, token=None):
		pass

	def peek_cq(self, cid=None, reservation_id=None, token=None):
		pass

	def register_event_notification(self, ep, evtype, class_id,
					token=None, reservation_id=None, filters=None):
		pass

	def cancel_task(self, cid=None, reservation_id=None, token=None,
			reason=None, qtask_id=None):
		pass

	def task_status(self, cid=None, reservation_id=None, token=None,
		    qtask_id=None):
		pass


class QPMAdmissionControl(QPMRemoteBase):
	def evaluate(self, token=None, request=None):
		pass

	def reserve(self, token=None, request=None):
		pass

	def renew(self, token=None, reservation_id=None, request=None):
		pass

	def release(self, token=None, reservation_id=None, reason=None):
		pass

	def cancel(self, token=None, reservation_id=None, reason=None):
		pass

	def get_reservation(self, token=None, reservation_id=None):
		pass

	def list_reservations(self, token=None, filters=None):
		pass

class QPMAdmissionPolicyConfig(QPMRemoteBase):
	def configure_device_profile(self, token=None, device_id=None,
				     profile=None):
		pass

	def get_device_profile(self, token=None, device_id=None):
		pass

	def get_admission_policy(self, token=None, device_id=None):
		pass

	def set_admission_policy(self, token=None, device_id=None,
				 configuration=None):
		pass


class QPMSchedulerControl(QPMRemoteBase):
	def configure_scheduler_policy(self, token=None, device_id=None,
				       configuration=None):
		pass

	def get_scheduler_status(self, token=None, device_id=None):
		pass

	def get_scheduler_policy(self, token=None, device_id=None):
		pass

	def pause_execution_target(self, token=None, device_id=None,
				   reason=None):
		pass

	def resume_execution_target(self, token=None, device_id=None):
		pass

	def drain_execution_target(self, token=None, device_id=None,
				   mode="graceful", timeout_s=None):
		pass

	def configure_dispatch_limits(self, token=None, device_id=None,
				      limits=None):
		pass

	def get_scheduler_queue_state(self, token=None, device_id=None,
				      include_restricted=False):
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

	def get_last_job_timing(self, cid=None, lib=None, reservation_id=None,
				token=None):
		pass

	def get_last_job_metadata(self, cid=None, lib=None, reservation_id=None,
				  token=None):
		pass

	def get_task_metadata(self, token=None, cid=None, reservation_id=None,
				      qtask_id=None):
		pass

	def get_telemetry_access_model(self, token=None):
		pass

	def get_capacity_snapshot(self, token=None, device_id=None, scope_id=None,
				  access_class=None):
		pass

	def get_queue_metrics(self, token=None, device_id=None, access_class=None):
		pass

	def reconcile_runtime_state(self, token=None, now_ns=None):
		pass

	def get_service_lifecycle_telemetry(self, token=None, access_class=None):
		pass

	def test(self):
		pass

	def shutdown(self):
		pass


class QPM(QPMExecution, QPMAdmissionControl, QPMAdmissionPolicyConfig,
	  QPMSchedulerControl, QPMTelemetry):
	pass
