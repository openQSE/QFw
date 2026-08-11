from api_qpm_common import QPMRemoteBase


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
