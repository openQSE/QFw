from api_qpm_common import QPMRemoteBase


class QPMControl(QPMRemoteBase):
	def test(self, token=None):
		pass

	def is_ready(self, token=None):
		pass

	def get_service_status(self, token=None):
		pass

	def get_service_summary(self, token=None):
		pass

	def reconcile_runtime_state(self, token=None, reason=None):
		pass

	def shutdown(self, token=None, mode="graceful", timeout_s=None,
		     reason=None):
		pass
