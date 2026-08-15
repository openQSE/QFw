from api_qpm_common import QPMRemoteBase


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
