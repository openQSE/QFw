from defw_exception import DEFwExecutionError
import json
import os
import subprocess
import yaml

DEVICE_ACCESS_CONFIG_ENV = "QFW_DEVICE_ACCESS_CFG"
QPU_DEVICE_ENV = "QFW_QPU_DEVICE_ID"


def services_dir():
	return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_device_access_config_path():
	return os.path.join(services_dir(), "dev-config", "config.yaml")


def device_access_config_path():
	return os.environ.get(
		DEVICE_ACCESS_CONFIG_ENV, default_device_access_config_path())


def load_yaml_config(path):
	try:
		with open(path, "r", encoding="utf-8") as stream:
			return yaml.safe_load(stream) or {}
	except FileNotFoundError as exc:
		raise DEFwExecutionError(
			f"QFw device access config file was not found: {path}") from exc
	except yaml.YAMLError as exc:
		raise DEFwExecutionError(
			f"failed to parse QFw device access config {path}: {exc}") \
			from exc


def load_json_config(path):
	try:
		with open(path, "r", encoding="utf-8") as stream:
			return json.load(stream)
	except FileNotFoundError as exc:
		raise DEFwExecutionError(
			f"QPU credential DB was not found: {path}") from exc
	except json.JSONDecodeError as exc:
		raise DEFwExecutionError(
			f"failed to parse QPU credential DB {path}: {exc}") from exc


def resolve_relative_path(path, base_path):
	if os.path.isabs(path):
		return path
	return os.path.join(os.path.dirname(os.path.abspath(base_path)), path)


def resolve_qpu_user():
	for name in ("QFW_USER", "SLURM_JOB_USER", "SLURM_USER",
			"USER", "LOGNAME"):
		value = os.environ.get(name)
		if value:
			return value.strip()
	try:
		result = subprocess.run(
			["whoami"],
			check=True,
			stdout=subprocess.PIPE,
			stderr=subprocess.PIPE,
			text=True)
	except Exception as exc:
		raise DEFwExecutionError(
			"failed to resolve QPU user. Set QFW_USER for device access.") \
			from exc

	value = result.stdout.strip()
	if value:
		return value
	raise DEFwExecutionError(
		"failed to resolve QPU user. Set QFW_USER for device access.")


def select_qpu(device_config, path, provider=None):
	qpus = device_config.get("qpus") or device_config.get("devices")
	if not isinstance(qpus, dict) or not qpus:
		raise DEFwExecutionError(
			f"QFw device access config {path} does not define any qpus")

	device_id = os.environ.get(QPU_DEVICE_ENV)
	if not device_id:
		if len(qpus) != 1:
			raise DEFwExecutionError(
				f"{QPU_DEVICE_ENV} must be set when multiple QPUs are "
				"configured")
		device_id = next(iter(qpus))

	device = qpus.get(device_id)
	if not isinstance(device, dict):
		raise DEFwExecutionError(
			f"QPU device {device_id!r} was not found in {path}")

	device_provider = str(device.get("provider", "")).lower()
	if provider and device_provider and device_provider != provider.lower():
		raise DEFwExecutionError(
			f"QPU service cannot use device {device_id!r} with provider "
			f"{device_provider!r}; expected {provider!r}")

	url = device.get("url")
	if not url:
		raise DEFwExecutionError(
			f"QPU device {device_id!r} in {path} does not define url")

	credential_db = (
		device.get("credential-db")
		or device.get("credential_db")
		or device_config.get("credential-db")
		or device_config.get("credential_db"))
	if not credential_db:
		raise DEFwExecutionError(
			f"QPU device {device_id!r} in {path} does not define "
			"credential-db")

	return {
		"device_id": device_id,
		"provider": device_provider or (provider.lower() if provider else ""),
		"url": str(url).strip(),
		"credential_db": resolve_relative_path(str(credential_db), path),
		"quantum_computer": (
			device.get("quantum-computer")
			or device.get("quantum_computer")),
	}


def get_user_records(credential_db):
	users = credential_db.get("users", credential_db)
	if not isinstance(users, dict):
		raise DEFwExecutionError("QPU credential DB users entry is invalid")
	return users


def get_api_key_from_user_record(record, device_id):
	if isinstance(record, str):
		return record.strip()
	if not isinstance(record, dict):
		return None

	devices = record.get("devices")
	if isinstance(devices, dict) and device_id in devices:
		device_record = devices[device_id]
		if isinstance(device_record, str):
			return device_record.strip()
		if isinstance(device_record, dict):
			value = device_record.get("api_key") or device_record.get("api-key")
			return str(value).strip() if value else None

	value = record.get("api_key") or record.get("api-key")
	return str(value).strip() if value else None


def resolve_qpu_credentials(device):
	user = resolve_qpu_user()
	credential_db = load_json_config(device["credential_db"])
	users = get_user_records(credential_db)
	record = users.get(user)
	if record is None:
		raise DEFwExecutionError(
			f"QPU credential DB does not contain credentials for user "
			f"{user!r}")

	api_key = get_api_key_from_user_record(record, device["device_id"])
	if not api_key:
		raise DEFwExecutionError(
			f"QPU credential DB does not contain an API key for user "
			f"{user!r} and device {device['device_id']!r}")

	return {
		"user": user,
		"api_key": api_key,
	}


def resolve_device_access(provider=None):
	path = device_access_config_path()
	device_config = load_yaml_config(path)
	device = select_qpu(device_config, path, provider=provider)
	credentials = resolve_qpu_credentials(device)

	return {
		"device_id": device["device_id"],
		"provider": device["provider"],
		"url": device["url"],
		"api_key": credentials["api_key"],
		"user": credentials["user"],
		"quantum_computer": device.get("quantum_computer"),
	}
