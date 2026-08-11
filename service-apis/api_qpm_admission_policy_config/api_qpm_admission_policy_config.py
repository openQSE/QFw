from api_qpm_common import QPMRemoteBase


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
