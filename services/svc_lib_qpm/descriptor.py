import os

QPU_DEVICE_ENV = "QFW_QPU_DEVICE_ID"
DEFAULT_PROVIDER = "iqm"

DEFAULT_CAPS = {
	"get_device_info": ["qdmi", "qrmi"],
	"get_coupling_graph": ["qdmi", "qrmi"],
	"get_calibration_snapshot": ["qdmi", "qrmi"],
	"get_dynamic_backend_info": ["qrmi"],
	"get_backend_info": ["qdmi", "qrmi"],
	# Execution is composable for the QRMI-vs-QDMI comparison: no lib routes to
	# the execution owner (qrmi); --lib qdmi runs the same circuit through QDMI.
	"run_circuit": ["qrmi", "qdmi"],
	"get_task_timing": ["qrmi", "qdmi"],
	"get_task_metadata": ["qrmi", "qdmi"],
}

DEFAULT_LIBRARIES = ["qrmi", "qdmi"]
DEFAULT_PREFERENCE = "qdmi"
DEFAULT_EXECUTION_OWNER = "qrmi"


def _as_list(value, default):
	if value is None:
		return list(default)
	if isinstance(value, str):
		return [item.strip() for item in value.split(",") if item.strip()]
	return list(value)


def _copy_caps(caps):
	source = caps or DEFAULT_CAPS
	return {call: list(libs) for call, libs in source.items()}


def _selected_device(device_id):
	from util.device_access import (
		device_access_config_path,
		load_yaml_config,
		select_qpu,
	)

	path = device_access_config_path()
	config = load_yaml_config(path)
	device_id = device_id or os.environ.get(QPU_DEVICE_ENV)
	# The provider hint only disambiguates between devices when none is named.
	# select_qpu also *validates* the selected device against it, so passing
	# the default once a device has been named rejected every non-IQM device
	# with "expected 'iqm'" no matter how it was configured. Once a device is
	# named, honor the provider that device declares.
	provider = None if device_id else DEFAULT_PROVIDER
	return select_qpu(config, path, provider=provider, device_id=device_id)


def resolve_descriptor(device_id=None):
	device = _selected_device(device_id or os.environ.get(QPU_DEVICE_ENV))

	return {
		"id": device["device_id"],
		"provider_device_id": device.get("provider_device_id"),
		"provider": device.get("provider") or DEFAULT_PROVIDER,
		"libraries": _as_list(device.get("libraries"), DEFAULT_LIBRARIES),
		"preference": device.get("preference", DEFAULT_PREFERENCE),
		"execution_owner": device.get(
			"execution_owner", DEFAULT_EXECUTION_OWNER),
		"caps": _copy_caps(device.get("caps")),
		# What happens to a circuit compiled for another calibration: require
		# or current. None takes util.isa's default for the marker's kind.
		"calibration_policy": device.get("calibration-policy"),
		# QRMI resource type. IBM QDMI accepts only IBMQiskitRuntimeService.
		# A QRMI provider with one resource type can omit this setting.
		"resource_type": (device.get("resource-type")
			or device.get("resource_type")),
		# IBM instance and IAM endpoint for QRMI's IBM resources and IBM QDMI.
		# Other providers ignore both settings.
		"service_crn": device.get("service-crn"),
		"iam_endpoint": device.get("iam-endpoint"),
		# The Pasqal Cloud project a job is billed to when the user's
		# credential DB entry names none. See drivers/qrmi_driver.py.
		"project_id": device.get("project-id"),
		# The device's API URL, which a Pasqal resource opens against when
		# nothing more specific names one.
		"url": device.get("url"),
		# Device-owned object storage for QRMI's IBMQuantumSystem.
		# Storage credentials stay in the credential DB.
		"s3_endpoint": device.get("s3-endpoint"),
		"s3_endpoint_for_qsapi": device.get("s3-endpoint-for-qsapi"),
		"s3_bucket": device.get("s3-bucket"),
		"s3_region": device.get("s3-region"),
		# QRMI IBM job timeout and all QDMI profiles' wait before cancellation.
		"job_timeout_seconds": device.get("job-timeout-seconds"),
		# The first wait between a job's status polls, which then doubles
		# (drivers/base_driver.py poll_schedule). None is 1 second.
		"poll_interval_seconds": device.get("poll-interval-seconds"),
		# QDMI catalogue entry, with a provider-specific default when omitted.
		"qdmi_device_id": device.get("qdmi-device-id"),
		# Optional Amazon Braket settings. The ARN is the provider-device-id.
		# Authentication uses the service process's AWS SDK credential chain.
		"aws_region": device.get("aws-region"),
		"s3_results_uri": device.get("s3-results-uri"),
		"reservation_arn": device.get("reservation-arn"),
		# What a service advertises and enforces for a device whose library
		# does not say up front: the qubit count for the directory record,
		# which is built before any session opens, and a shot cap the driver
		# refuses above, before anything is billed.
		"num_qubits": device.get("num-qubits"),
		"max_shots": device.get("max-shots"),
		# How many of the device's circuits may be at the provider at once,
		# for a provider that rate-limits. The QPM holds the rest in its
		# queue (svc_qpm.py). None adds no limit of the device's own.
		"max_concurrent_jobs": device.get("max-concurrent-jobs"),
	}
