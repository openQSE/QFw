def test_qpm_compatibility_surface_keeps_existing_methods():
	from api_qpm import QPM

	for method_name in (
		"sync_run",
		"async_run",
		"read_cq",
		"peek_cq",
		"register_event_notification",
		"cancel_task",
		"task_status",
		"get_backend_info",
		"get_device_info",
		"shutdown",
	):
		assert hasattr(QPM, method_name)


def test_qpm_category_surfaces_are_importable():
	from api_qpm import (
		QPM,
		QPMAdmissionControl,
		QPMAdmissionPolicyConfig,
		QPMExecution,
		QPMSchedulerControl,
		QPMTelemetry,
	)

	assert issubclass(QPM, QPMExecution)
	assert issubclass(QPM, QPMAdmissionControl)
	assert issubclass(QPM, QPMAdmissionPolicyConfig)
	assert issubclass(QPM, QPMSchedulerControl)
	assert issubclass(QPM, QPMTelemetry)
	assert hasattr(QPMAdmissionControl, "reserve")
	assert hasattr(QPMAdmissionPolicyConfig, "set_admission_policy")
	assert hasattr(QPMSchedulerControl, "set_scheduler_policy")
	assert hasattr(QPMTelemetry, "get_calibration_snapshot")


def test_qpm_category_service_api_packages_export_single_surface():
	import api_qpm_admission_control
	import api_qpm_admission_policy_config
	import api_qpm_execution
	import api_qpm_scheduler_control
	import api_qpm_telemetry

	assert api_qpm_execution.svc_info["category"] == "execution"
	assert api_qpm_execution.service_classes[0].__name__ == "QPMExecution"
	assert (
		api_qpm_admission_control.svc_info["category"] == "admission"
	)
	assert (
		api_qpm_admission_policy_config.svc_info["category"] ==
		"admission-policy"
	)
	assert api_qpm_scheduler_control.svc_info["category"] == "scheduler"
	assert api_qpm_telemetry.svc_info["category"] == "telemetry"
