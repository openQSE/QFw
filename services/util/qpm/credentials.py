from dataclasses import dataclass
import importlib
import os
import time

from defw_exception import DEFwExecutionError

from .. import device_access


CREDENTIAL_BINDING_SCHEMA = "qfw-provider-credential-binding-v1"
CREDENTIAL_PROVIDER_CONFIG_KEYS = (
	"credential_provider",
)
FILE_PROVIDER_TYPES = ("file", "json", "file-backed", "development-file")
NO_SECRET_PROVIDER = "no-secret"
CREDENTIAL_MODE_ENV = "QFW_QPM_CREDENTIAL_MODE"


class QPMCredentialError(DEFwExecutionError):
	pass


class QPMCredentialBindingMissing(QPMCredentialError):
	pass


class QPMCredentialProviderUnavailable(QPMCredentialError):
	pass


@dataclass(frozen=True)
class CredentialProviderResponse:
	secret: dict
	metadata: dict


class CredentialProvider:
	name = "base"

	def validate(self, request):
		raise NotImplementedError

	def bind(self, request):
		raise NotImplementedError

	def release(self, binding):
		return None

	def refresh(self, binding):
		return binding


class NoSecretCredentialProvider(CredentialProvider):
	name = NO_SECRET_PROVIDER

	def validate(self, request):
		return None

	def bind(self, request):
		now_ns = time.time_ns()
		return CredentialProviderResponse(
			secret={},
			metadata={
				"schema": CREDENTIAL_BINDING_SCHEMA,
				"provider": self.name,
				"provider_type": self.name,
				"credential_scope": request.get("credential_scope"),
				"credential_handle": request.get("credential_handle"),
				"target_device_id": request.get("target_device_id"),
				"provider_device_id": request.get("provider_device_id"),
				"user": request.get("user"),
				"bound_at_ns": now_ns,
				"expires_at_ns": 0,
				"refresh_policy": "none",
				"secret_material": "none",
			})


class FileCredentialProvider(CredentialProvider):
	name = "file"

	def __init__(self, config_path, device, provider_config=None):
		self.config_path = config_path
		self.device = dict(device or {})
		self.provider_config = dict(provider_config or {})

	def validate(self, request):
		self._select_credential(request)

	def bind(self, request):
		record_key, api_key, credential_db_path = self._select_credential(
			request)
		now_ns = time.time_ns()
		expires_at_ns = self._expires_at_ns(now_ns)
		metadata = {
			"schema": CREDENTIAL_BINDING_SCHEMA,
			"provider": self.provider_config.get("name", self.name),
			"provider_type": self.name,
			"credential_scope": (
				request.get("credential_scope") or
				self.provider_config.get("scope")),
			"credential_handle": request.get("credential_handle"),
			"credential_hint": _redacted_hint(request.get("credential_hint")),
			"target_device_id": self.device.get("device_id"),
			"provider_device_id": self.device.get("provider_device_id"),
			"user": record_key,
			"bound_at_ns": now_ns,
			"expires_at_ns": expires_at_ns,
			"refresh_policy": self.provider_config.get(
				"refresh-policy", "none"),
			"source": {
				"type": "file",
				"config": self.config_path,
				"credential_db": credential_db_path,
			},
			"secret_material": "cached-in-qpm",
		}
		secret = {
			"api_key": api_key,
			"token": api_key,
			"url": self.device.get("url"),
			"device_id": self.device.get("device_id"),
			"provider": self.device.get("provider"),
			"provider_device_id": self.device.get("provider_device_id"),
			"quantum_computer": self.device.get("provider_device_id"),
			"user": record_key,
		}
		return CredentialProviderResponse(
			secret={key: value for key, value in secret.items()
				if value not in (None, "")},
			metadata=_drop_none(metadata))

	def _select_credential(self, request):
		credential_db_path = self._credential_db_path()
		credential_db = device_access.load_json_config(credential_db_path)
		record_key, user_record = device_access.select_user_record(
			credential_db,
			request.get("user"),
			device_id=self.device.get("device_id"),
			provider_device_id=self.device.get("provider_device_id"),
			credential_hint=request.get("credential_hint"),
			credential_handle=request.get("credential_handle"))
		api_key = device_access.get_api_key_from_user_record(
			user_record,
			self.device.get("device_id"),
			self.device.get("provider_device_id"))
		if not api_key:
			raise QPMCredentialBindingMissing(
				"file credential provider did not return an API key for "
				f"user={record_key!r} device={self.device.get('device_id')!r}")
		return record_key, api_key, credential_db_path

	def _credential_db_path(self):
		value = (
			self.provider_config.get("credential-db") or
			self.provider_config.get("path") or
			self.device.get("credential_db"))
		if not value:
			raise QPMCredentialProviderUnavailable(
				"file credential provider requires credential-db")
		return device_access.resolve_relative_path(value, self.config_path)

	def _expires_at_ns(self, now_ns):
		ttl_ns = self.provider_config.get("ttl-ns")
		if ttl_ns is None:
			ttl_s = self.provider_config.get("ttl-seconds")
			if ttl_s is None:
				return 0
			ttl_ns = int(float(ttl_s) * 1_000_000_000)
		ttl_ns = int(ttl_ns)
		if ttl_ns <= 0:
			return 0
		return now_ns + ttl_ns


def bind_reservation_credential(binding, credential_mode=None):
	request = credential_request_from_binding(binding)
	provider = provider_for_request(request, credential_mode=credential_mode)
	return provider, provider.bind(request)


def validate_reservation_credential(binding, credential_mode=None):
	request = credential_request_from_binding(binding)
	provider = provider_for_request(request, credential_mode=credential_mode)
	provider.validate(request)


def credential_request_from_binding(binding):
	binding = dict(binding or {})
	resource = dict(binding.get("resource") or {})
	launcher = dict(binding.get("launcher") or {})
	credential = dict(binding.get("provider_credential_binding") or {})
	owner = dict(binding.get("owner") or {})
	user = (
		launcher.get("external_user_id") or
		owner.get("user") or
		owner.get("user_id") or
		credential.get("user_id"))
	return {
		"binding": binding,
		"user": user,
		"job_id": launcher.get("external_job_id"),
		"allocation_id": launcher.get("allocation_id"),
		"scope_id": launcher.get("external_scope_id") or resource.get("scope_id"),
		"target_device_id": (
			resource.get("target_device_id") or
			credential.get("target_device_id")),
		"provider_device_id": credential.get("provider_device_id"),
		"provider": credential.get("provider"),
		"credential_scope": credential.get("credential_scope"),
		"credential_hint": credential.get("credential_hint"),
		"credential_handle": credential.get("credential_handle"),
	}


def provider_for_request(request, credential_mode=None):
	credential_mode = (
		credential_mode or os.environ.get(CREDENTIAL_MODE_ENV) or ""
	).strip().lower()
	if credential_mode == NO_SECRET_PROVIDER:
		return NoSecretCredentialProvider()
	if credential_mode != "required":
		raise QPMCredentialProviderUnavailable(
			"QPM credential mode must be explicitly configured")
	config_path = device_access.device_access_config_path()
	try:
		config = device_access.load_yaml_config(config_path)
	except DEFwExecutionError as exc:
		raise QPMCredentialProviderUnavailable(
			"credential provider configuration is required but could not "
			f"be loaded: {exc}") from exc
	device = _selected_device(config, config_path, request)
	if device is None:
		raise QPMCredentialProviderUnavailable(
			"no matching QPU device was found for target "
			f"{request.get('target_device_id')!r}")
	provider_config = _provider_config_for_device(config, device)
	provider_type = str(provider_config.get("type", "file")).strip().lower()
	if provider_type in FILE_PROVIDER_TYPES:
		return FileCredentialProvider(config_path, device, provider_config)
	if provider_type in ("none", "no-secret"):
		raise QPMCredentialProviderUnavailable(
			"hardware QPM cannot use a no-secret credential provider")
	if provider_type in ("python", "plugin", "module"):
		return _load_plugin_provider(provider_config, config_path, device)
	raise QPMCredentialProviderUnavailable(
		f"unsupported credential provider type {provider_type!r}")


def _selected_device(config, config_path, request):
	target_device_id = request.get("target_device_id")
	provider = request.get("provider")
	try:
		return device_access.select_qpu(
			config, config_path, provider=provider, device_id=target_device_id)
	except DEFwExecutionError:
		if target_device_id:
			try:
				return device_access.select_qpu(
					config, config_path, device_id=target_device_id)
			except DEFwExecutionError:
				return None
		return None


def _provider_config_for_device(config, device):
	providers = (
		config.get("credential-providers") or {})
	provider_ref = None
	for key in CREDENTIAL_PROVIDER_CONFIG_KEYS:
		if key in device:
			provider_ref = device.get(key)
			break
	if isinstance(provider_ref, dict):
		provider_config = dict(provider_ref)
	elif provider_ref:
		provider_config = dict(providers.get(provider_ref, {}))
		provider_config.setdefault("name", provider_ref)
	else:
		provider_config = {}
	if not provider_config:
		provider_config = {
			"type": "file",
			"credential_db": device.get("credential_db"),
		}
	provider_config.setdefault("type", "file")
	return provider_config


def _load_plugin_provider(provider_config, config_path, device):
	module_name = (
		provider_config.get("module"))
	class_name = (
		provider_config.get("class") or
		"CredentialProvider")
	if not module_name:
		raise QPMCredentialProviderUnavailable(
			"python credential provider requires module")
	try:
		module = importlib.import_module(module_name)
		provider_class = getattr(module, class_name)
		return provider_class(provider_config, config_path, device)
	except Exception as exc:
		raise QPMCredentialProviderUnavailable(
			"failed to load credential provider "
			f"{module_name}.{class_name}: {exc}") from exc


def _credential_context_required(request):
	return any(request.get(key) for key in (
		"credential_hint",
		"credential_handle",
		"credential_scope",
	))


def _redacted_hint(value):
	if isinstance(value, dict):
		return {
			key: ("<redacted>" if _sensitive_key(key) else _redacted_hint(item))
			for key, item in value.items()
		}
	return value


def _sensitive_key(key):
	name = str(key).strip().lower().replace("-", "_")
	for part in ("api_key", "apikey", "password", "secret", "token"):
		if part in name:
			return True
	return False


def _drop_none(value):
	if isinstance(value, dict):
		return {
			key: _drop_none(item)
			for key, item in value.items()
			if item is not None
		}
	if isinstance(value, list):
		return [_drop_none(item) for item in value if item is not None]
	return value
