from api_qpm_common import QPMRemoteBase


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

	def capability_map(self, token=None):
		pass

	def get_telemetry_access_model(self, token=None):
		pass

	def get_capacity_snapshot(self, token=None, device_id=None, scope_id=None,
				  access_class=None):
		pass

	def get_queue_metrics(self, token=None, device_id=None, access_class=None):
		pass

	def get_service_lifecycle_telemetry(self, token=None, access_class=None):
		pass

	def list_scheduler_allocations(self, token=None, filters=None):
		pass
