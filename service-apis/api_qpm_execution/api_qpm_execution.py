from api_qpm_common import QPMRemoteBase


class QPMExecution(QPMRemoteBase):
	def delete_circuit(self, cid, reservation_id=None, token=None):
		pass

	def sync_run(self, info, reservation_id=None, token=None, timeout=None,
			 cancel_on_timeout=False):
		pass

	def async_run(self, info, reservation_id=None, token=None, timeout=None,
			  cancel_on_timeout=False):
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

	def get_task_timing(self, token=None, reservation_id=None, task_id=None):
		pass

	def get_task_metadata(self, token=None, reservation_id=None, task_id=None):
		pass
