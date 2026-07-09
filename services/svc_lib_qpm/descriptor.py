import os

QPU_DEVICE_ENV = "QFW_QPU_DEVICE_ID"
DEFAULT_PROVIDER = "iqm"

DEFAULT_CAPS = {
	"get_device_info": ["qdmi", "qrmi"],
	"get_coupling_graph": ["qdmi", "qrmi"],
	"get_calibration_snapshot": ["qdmi", "qrmi"],
	"get_dynamic_backend_info": ["qrmi"],
	"get_backend_info": ["qrmi"],
	# Execution is composable for the QRMI-vs-QDMI comparison: no lib routes to
	# the execution owner (qrmi); --lib qdmi runs the same circuit through QDMI.
	"run_circuit": ["qrmi", "qdmi"],
	"get_last_job_timing": ["qrmi", "qdmi"],
	"get_last_job_metadata": ["qrmi", "qdmi"],
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
	if device_id:
		old = os.environ.get(QPU_DEVICE_ENV)
		os.environ[QPU_DEVICE_ENV] = device_id
		try:
			return select_qpu(config, path, provider=DEFAULT_PROVIDER)
		finally:
			if old is None:
				os.environ.pop(QPU_DEVICE_ENV, None)
			else:
				os.environ[QPU_DEVICE_ENV] = old
	return select_qpu(config, path, provider=DEFAULT_PROVIDER)


def resolve_descriptor(device_id=None):
	device = _selected_device(device_id or os.environ.get(QPU_DEVICE_ENV))

	return {
		"id": device["device_id"],
		"provider_device_id": device.get("provider_device_id"),
		"provider": device.get("provider") or DEFAULT_PROVIDER,
		"libraries": _as_list(device.get("libraries"), DEFAULT_LIBRARIES),
		"preference": device.get("preference", DEFAULT_PREFERENCE),
		"execution_owner": device.get(
			"execution-owner",
			device.get("execution_owner", DEFAULT_EXECUTION_OWNER)),
		"caps": _copy_caps(device.get("caps")),
	}
