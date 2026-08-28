from defw_exception import DEFwExecutionError
import json
import os
import subprocess
import yaml

DEVICE_ACCESS_CONFIG_ENV = "QFW_DEVICE_ACCESS_CFG"
QPU_DEVICE_ENV = "QFW_QPU_DEVICE_ID"

REMOVED_TOP_LEVEL_KEYS = {
	"devices": "qpus",
	"credential_providers": "credential-providers",
}
REMOVED_DEVICE_KEYS = {
	"provider_device_id": "provider-device-id",
	"quantum-computer": "provider-device-id",
	"quantum_computer": "provider-device-id",
	"credential_db": "credential-db",
	"credential_provider": "credential-provider",
	"execution_owner": "execution-owner",
}
REMOVED_CREDENTIAL_PROVIDER_KEYS = {
	"credential_db": "credential-db",
	"refresh_policy": "refresh-policy",
	"ttl_ns": "ttl-ns",
	"ttl_seconds": "ttl-seconds",
	"plugin_module": "module",
	"class_name": "class",
}


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


def validate_credential_configuration(path, device_id):
	config = load_yaml_config(path)
	device = select_qpu(config, path, device_id=device_id)
	provider_name = device.get("credential_provider")
	if provider_name:
		providers = config.get("credential-providers") or {}
		provider = providers.get(provider_name)
		if not isinstance(provider, dict) or not provider:
			raise DEFwExecutionError(
				f"credential provider {provider_name!r} for QPU "
				f"{device_id!r} is not configured")
		provider_type = str(provider.get("type") or "").strip().lower()
		if provider_type in ("none", "no-secret"):
			raise DEFwExecutionError(
				f"QPU {device_id!r} requires a credential provider")
	return device


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


def _reject_removed_keys(mapping, removed_keys, context):
	for key, replacement in removed_keys.items():
		if key in mapping:
			raise DEFwExecutionError(
				f"unsupported {context} key {key!r}; use {replacement!r}")


def select_qpu(device_config, path, provider=None, device_id=None):
	_reject_removed_keys(
		device_config, REMOVED_TOP_LEVEL_KEYS,
		f"QFw device access config {path}")
	qpus = device_config.get("qpus")
	if not isinstance(qpus, dict) or not qpus:
		raise DEFwExecutionError(
			f"QFw device access config {path} does not define any qpus")

	for candidate_id, candidate in qpus.items():
		if isinstance(candidate, dict):
			_reject_removed_keys(
				candidate, REMOVED_DEVICE_KEYS,
				f"QPU device {candidate_id!r}")
	credential_providers = device_config.get("credential-providers") or {}
	if not isinstance(credential_providers, dict):
		raise DEFwExecutionError(
			f"QFw device access config {path} credential-providers must be a "
			"mapping")
	for provider_name, provider_config in credential_providers.items():
		if isinstance(provider_config, dict):
			_reject_removed_keys(
				provider_config, REMOVED_CREDENTIAL_PROVIDER_KEYS,
				f"credential provider {provider_name!r}")

	provider = provider.lower() if provider else None
	device_id = device_id or os.environ.get(QPU_DEVICE_ENV)
	if not device_id:
		if provider:
			matches = [
				(candidate_id, candidate)
				for candidate_id, candidate in qpus.items()
				if (isinstance(candidate, dict) and
					str(candidate.get("provider", "")).lower() == provider)
			]
			if len(matches) == 1:
				device_id, device = matches[0]
			elif len(matches) > 1:
				raise DEFwExecutionError(
					f"{QPU_DEVICE_ENV} must be set when multiple QPUs "
					f"are configured for provider {provider!r}")
			else:
				raise DEFwExecutionError(
					f"QFw device access config {path} does not define "
					f"a QPU for provider {provider!r}")
		if device_id:
			pass
		elif len(qpus) != 1:
			raise DEFwExecutionError(
				f"{QPU_DEVICE_ENV} must be set when multiple QPUs are "
				"configured")
		else:
			device_id = next(iter(qpus))

	device = qpus.get(device_id)
	if not isinstance(device, dict):
		for candidate_id, candidate in qpus.items():
			if not isinstance(candidate, dict):
				continue
			provider_device_id = candidate.get("provider-device-id")
			aliases = candidate.get("aliases", [])
			if isinstance(aliases, str):
				aliases = [aliases]
			matches = {
				str(candidate_id),
				str(provider_device_id) if provider_device_id else "",
				*(str(item) for item in aliases),
			}
			if str(device_id) in matches:
				device = candidate
				device_id = candidate_id
				break
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

	credential_db = device.get("credential-db")
	credential_provider = device.get("credential-provider")
	if not credential_db and not credential_provider:
		raise DEFwExecutionError(
			f"QPU device {device_id!r} in {path} does not define "
			"credential-db or credential-provider")

	provider_device_id = device.get("provider-device-id") or device_id

	selected = {
		"device_id": device_id,
		"provider_device_id": str(provider_device_id).strip(),
		"provider": device_provider or (provider.lower() if provider else ""),
		"url": str(url).strip(),
		"quantum_computer": str(provider_device_id).strip(),
		"credential_provider": credential_provider,
	}
	if credential_db:
		selected["credential_db"] = resolve_relative_path(
			str(credential_db), path)

	# Pass through the optional shim descriptor fields (svc_lib_qpm's
	# resolve_descriptor reads these off the selected device; see
	# docs/qpu-frontend-contract.md section 5). Only forward keys that are
	# actually configured so descriptor.py can apply its own defaults for the
	# rest -- a key present with a None value would otherwise defeat the
	# `device.get(key, DEFAULT)` fallbacks. The native resolve_device_access
	# path ignores these keys, so forwarding them here is harmless.
	for key in ("libraries", "preference", "caps"):
		if key in device:
			selected[key] = device[key]
	if "execution-owner" in device:
		selected["execution_owner"] = device["execution-owner"]

	return selected


def get_user_records(credential_db):
	users = credential_db.get("users", credential_db)
	if not isinstance(users, dict):
		raise DEFwExecutionError("QPU credential DB users entry is invalid")
	return users


def select_user_record(
		credential_db, user, device_id=None, provider_device_id=None,
		credential_hint=None, credential_handle=None):
	users = get_user_records(credential_db)
	for record_key in _credential_record_candidates(
			users, user, credential_hint, credential_handle):
		record = users.get(record_key)
		if record is None:
			continue
		if get_api_key_from_user_record(record, device_id, provider_device_id):
			return record_key, record
	raise DEFwExecutionError(
		f"QPU credential DB does not contain an enabled entitlement and "
		f"API key for user {user!r} and device {device_id!r}")


def _credential_record_candidates(
		users, user, credential_hint=None, credential_handle=None):
	candidates = []
	for value in (
			_credential_handle_user(users, credential_handle),
			_credential_hint_user(credential_hint),
			credential_handle if isinstance(credential_handle, str) else None,
			credential_hint if isinstance(credential_hint, str) else None,
			user):
		if value is None:
			continue
		value = str(value).strip()
		if value and value not in candidates:
			candidates.append(value)
	return candidates


def _credential_handle_user(users, credential_handle):
	if not credential_handle:
		return None
	handles = None
	if isinstance(users, dict):
		handles = users.get("handles")
	if not isinstance(handles, dict):
		return None
	entry = handles.get(str(credential_handle))
	if isinstance(entry, str):
		return entry
	if isinstance(entry, dict):
		return (
			entry.get("user")
			or entry.get("user_record")
			or entry.get("credential_user"))
	return None


def _credential_hint_user(credential_hint):
	if not isinstance(credential_hint, dict):
		return None
	return (
		credential_hint.get("user")
		or credential_hint.get("user_record")
		or credential_hint.get("credential_user")
		or credential_hint.get("record"))


def get_api_key_from_user_record(record, device_id, provider_device_id=None):
	if not isinstance(record, dict):
		return None
	if record.get("enabled") is not True:
		return None

	devices = record.get("devices")
	if isinstance(devices, dict):
		for key in (device_id, provider_device_id):
			if not key or key not in devices:
				continue
			device_record = devices[key]
			if isinstance(device_record, dict):
				if device_record.get("enabled") is not True:
					return None
				value = device_record.get("api_key")
				return str(value).strip() if value else None
	return None


def resolve_qpu_credentials(device, user=None, credential_hint=None,
			    credential_handle=None):
	user = user or resolve_qpu_user()
	credential_db = load_json_config(device["credential_db"])
	user, record = select_user_record(
		credential_db,
		user,
		device_id=device["device_id"],
		provider_device_id=device.get("provider_device_id"),
		credential_hint=credential_hint,
		credential_handle=credential_handle)

	api_key = get_api_key_from_user_record(
		record, device["device_id"], device.get("provider_device_id"))
	if not api_key:
		raise DEFwExecutionError(
			f"QPU credential DB does not contain an API key for user "
			f"{user!r} and device {device['device_id']!r}")

	return {
		"user": user,
		"api_key": api_key,
	}


def resolve_device_access(provider=None, device_id=None, user=None,
			  credential_hint=None, credential_handle=None):
	path = device_access_config_path()
	device_config = load_yaml_config(path)
	device = select_qpu(
		device_config, path, provider=provider, device_id=device_id)
	credentials = resolve_qpu_credentials(
		device,
		user=user,
		credential_hint=credential_hint,
		credential_handle=credential_handle)

	return {
		"device_id": device["device_id"],
		"provider_device_id": device["provider_device_id"],
		"provider": device["provider"],
		"url": device["url"],
		"api_key": credentials["api_key"],
		"user": credentials["user"],
		"quantum_computer": device["provider_device_id"],
	}
