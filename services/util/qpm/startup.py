import json
import logging
import os
import threading
import uuid
from pathlib import Path
from time import monotonic, sleep, time_ns

import util.qpm.util_qpm as uq
from .controller import find_target_controller


OPERATION_MODE_ENV = "QFW_QPM_OPERATION_MODE"
REGISTER_WITH_DIRSVC_ENV = "QFW_QPM_REGISTER_WITH_DIRSVC"
DIRECT_ENDPOINT_ENABLED_ENV = "QFW_QPM_DIRECT_ENDPOINT_ENABLED"
DIRECT_QPM_ENDPOINT_ENV = "QFW_DIRECT_QPM_ENDPOINT"
SITE_DIRSVC_ENDPOINTS_ENV = "QFW_SITE_DIRSVC_ENDPOINTS"
STARTUP_TIMEOUT_ENV = "QFW_STARTUP_TIMEOUT"
LOCAL_DIRSVC_ENDPOINT_ENV = "QFW_LOCAL_DIRSVC_ENDPOINT"
SERVICE_READY_FILE_ENV = "QFW_SERVICE_READY_FILE"

DEFAULT_OPERATION_MODE = "qfw-managed"
LONG_RUNNING_OPERATION_MODE = "long-running"
DEFAULT_STARTUP_TIMEOUT = 40
ZERO_UUID = str(uuid.UUID(int=0))
SITE_REGISTRATION_STATE_ATTR = "_qfw_site_dirsvc_registrations"
LOCAL_REGISTRATION_STATE_ATTR = "_qfw_local_dirsvc_registrations"

FALSE_VALUES = {"0", "false", "no", "off", "n"}
TRUE_VALUES = {"1", "true", "yes", "on", "y"}


def startup_timeout():
	try:
		return int(os.environ.get(STARTUP_TIMEOUT_ENV, DEFAULT_STARTUP_TIMEOUT))
	except ValueError:
		return DEFAULT_STARTUP_TIMEOUT


def _env_flag(name, default=None):
	value = os.environ.get(name)
	if value is None:
		return default
	value = value.strip().lower()
	if value in TRUE_VALUES:
		return True
	if value in FALSE_VALUES:
		return False
	return default


def direct_endpoint_enabled():
	return bool(_env_flag(DIRECT_ENDPOINT_ENABLED_ENV, False))


def operation_mode():
	return os.environ.get(
		OPERATION_MODE_ENV,
		DEFAULT_OPERATION_MODE,
	).strip().lower()


def long_running_mode_enabled():
	return operation_mode() == LONG_RUNNING_OPERATION_MODE


def site_dirsvc_endpoints_configured():
	return bool(_site_dirsvc_endpoints())


def register_with_dirsvc():
	configured = _env_flag(REGISTER_WITH_DIRSVC_ENV, None)
	if configured is not None:
		return configured
	if direct_endpoint_enabled():
		return False
	if long_running_mode_enabled():
		return site_dirsvc_endpoints_configured()
	return True


def startup_config():
	return {
		"operation_mode": os.environ.get(
			OPERATION_MODE_ENV,
			DEFAULT_OPERATION_MODE,
		).strip().lower(),
		"register_with_dirsvc": register_with_dirsvc(),
		"direct_endpoint_enabled": direct_endpoint_enabled(),
		"direct_qpm_endpoint": os.environ.get(DIRECT_QPM_ENDPOINT_ENV, ""),
		"site_dirsvc_endpoints": os.environ.get(SITE_DIRSVC_ENDPOINTS_ENV, ""),
	}


def startup_status(defw_module):
	status = startup_config()
	status.update({
		"listener_ready": listener_ready(defw_module),
		"controller_ready": controller_ready(defw_module),
		"site_dirsvc_ready": _site_dirsvc_ready(defw_module),
		"local_registration_required": _local_registration_required(),
		"local_registration_ready": _local_registration_complete(defw_module),
		"site_registration_required": _site_registration_required(),
		"site_registration_ready": _site_registration_complete(defw_module),
	})
	return status


def should_wait_for_dirsvc(defw_module):
	if not register_with_dirsvc():
		return False
	return not _dirsvc_ready(defw_module)


def listener_ready(defw_module):
	return _readiness_hook(defw_module, "qpm_listener_ready")


def controller_ready(defw_module):
	return _readiness_hook(defw_module, "qpm_controller_ready")


def _readiness_hook(defw_module, method_name):
	method = getattr(defw_module, method_name, None)
	if method is None:
		return True
	return bool(method())


def _listener_and_controller_ready(defw_module):
	return listener_ready(defw_module) and controller_ready(defw_module)


def _startup_wait_reason(defw_module):
	if not listener_ready(defw_module):
		return "listener"
	if not controller_ready(defw_module):
		return "controller"
	if should_wait_for_dirsvc(defw_module):
		return "dirsvc"
	if _local_registration_required() and \
	   not _local_registration_ready(defw_module):
		return "dirsvc-registration"
	if _site_registration_required() and \
	   not _site_registration_ready(defw_module):
		return "dirsvc"
	return None


def _dirsvc_ready(defw_module):
	if long_running_mode_enabled():
		if not site_dirsvc_endpoints_configured():
			return True
		return _site_dirsvc_ready(defw_module)
	return getattr(defw_module, "dirsvc", None) is not None


def _site_dirsvc_ready(defw_module):
	endpoints = _site_dirsvc_endpoints()
	if not endpoints:
		return True
	return all(_site_dirsvc_endpoint_ready(defw_module, endpoint)
		for endpoint in endpoints)


def _site_dirsvc_endpoint_ready(defw_module, endpoint):
	return _site_dirsvc_client(defw_module, endpoint) is not None


def _site_registration_required():
	return (
		long_running_mode_enabled() and
		register_with_dirsvc() and
		site_dirsvc_endpoints_configured()
	)


def _local_registration_required():
	return register_with_dirsvc() and not long_running_mode_enabled()


def _local_registration_ready(defw_module):
	if not _local_registration_required():
		return True
	if not _listener_and_controller_ready(defw_module):
		return False
	if not _dirsvc_ready(defw_module):
		return False
	return _ensure_local_registration(defw_module)


def _local_registration_complete(defw_module):
	if not _local_registration_required():
		return True
	endpoint = _local_dirsvc_endpoint()
	state = getattr(defw_module, LOCAL_REGISTRATION_STATE_ATTR, {})
	if not isinstance(state, dict):
		return False
	return endpoint in state


def _ensure_local_registration(defw_module):
	records = _site_registration_records(defw_module)
	if not records:
		logging.error("no QPM service records available for local registration")
		return False

	endpoint = _local_dirsvc_endpoint()
	state = getattr(defw_module, LOCAL_REGISTRATION_STATE_ATTR, None)
	if not isinstance(state, dict):
		state = {}
		setattr(defw_module, LOCAL_REGISTRATION_STATE_ATTR, state)
	if endpoint in state:
		return True

	client = getattr(defw_module, "dirsvc", None)
	if client is None:
		return False
	peer = _site_registration_peer(defw_module)
	registered = []
	for record in records:
		try:
			registered_record = _register_site_record(
				client, record, defw_module)
			registered_records = _as_list(registered_record)
			registered.extend(registered_records)
			for lifecycle_record in registered_records or [record]:
				_record_site_registration_lifecycle(
					record, lifecycle_record, peer, endpoint)
		except Exception:
			logging.exception(
				"failed to register QPM service with local dirsvc")
			return False
	state[endpoint] = registered
	return True


def _local_dirsvc_endpoint():
	return os.environ.get(LOCAL_DIRSVC_ENDPOINT_ENV, "allocation-local")


def _site_registration_ready(defw_module):
	if not _site_registration_required():
		return True
	if not _listener_and_controller_ready(defw_module):
		return False
	if not _site_dirsvc_ready(defw_module):
		return False
	return _ensure_site_registration(defw_module)


def _site_registration_complete(defw_module):
	if not _site_registration_required():
		return True
	state = getattr(defw_module, SITE_REGISTRATION_STATE_ATTR, {})
	if not isinstance(state, dict):
		return False
	return all(endpoint in state for endpoint in _site_dirsvc_endpoints())


def _ensure_site_registration(defw_module):
	records = _site_registration_records(defw_module)
	if not records:
		logging.error("no QPM service records available for site registration")
		return False

	state = getattr(defw_module, SITE_REGISTRATION_STATE_ATTR, None)
	if not isinstance(state, dict):
		state = {}
		setattr(defw_module, SITE_REGISTRATION_STATE_ATTR, state)

	peer = _site_registration_peer(defw_module)
	for endpoint in _site_dirsvc_endpoints():
		if endpoint in state:
			continue
		client = _site_dirsvc_client(defw_module, endpoint)
		if client is None:
			logging.error("site dirsvc %s is ready but no client is available",
				      endpoint)
			return False
		registered = []
		for record in records:
			try:
				registered_record = _register_site_record(
					client, record, defw_module)
				registered_records = _as_list(registered_record)
				registered.extend(registered_records)
				for lifecycle_record in registered_records or [record]:
					_record_site_registration_lifecycle(
						record, lifecycle_record, peer, endpoint)
			except Exception:
				logging.exception(
					"failed to register QPM service with site dirsvc")
				return False
		state[endpoint] = registered
	return True


def _site_registration_records(defw_module):
	records = _call_or_read(defw_module, "qpm_site_service_records")
	if records:
		return [dict(record) for record in _as_list(records)]

	advertisements = _query_local_service_metadata(defw_module)
	return [
		_service_record(defw_module, advertisement)
		for advertisement in advertisements
	]


def _call_or_read(obj, name):
	value = getattr(obj, name, None)
	if value is None:
		return None
	return value() if callable(value) else value


def _query_local_service_metadata(defw_module):
	services = getattr(defw_module, "services", None)
	if services is None:
		return []
	advertisements = []
	for _svc, module in services:
		svc_info = getattr(module, "svc_info", {})
		if svc_info.get("name") == "Directory Service":
			continue
		for service_class in getattr(module, "service_classes", []):
			try:
				obj = service_class(start=False)
				metadata = obj.query()
			except Exception:
				logging.exception("failed to query QPM service metadata")
				continue
			advertisements.extend(_as_list(metadata))
	return advertisements


def _service_record(defw_module, advertisement):
	if not isinstance(advertisement, dict):
		raise TypeError("QPM service query must return a metadata dictionary")
	advertisement = dict(advertisement)
	properties = dict(advertisement.get("properties") or {})
	lifecycle_service_id = os.environ.get("QFW_QPM_SERVICE_ID")
	service_id = lifecycle_service_id or advertisement.get("service_id") or \
		properties.get("service_id")
	if service_id:
		properties["service_id"] = service_id
	service_name = advertisement.get("service_name")
	if not service_name:
		raise ValueError("QPM service metadata missing service_name")
	endpoint = _defw_endpoint(defw_module)
	endpoint_record = _endpoint_record(endpoint)
	qpm_type = advertisement.get(
		"qpm_type", properties.get("qpm_type", -1))
	qpm_capabilities = advertisement.get(
		"qpm_capabilities", properties.get("qpm_capabilities", -1))
	if qpm_type != -1:
		properties.setdefault("qpm_type", qpm_type)
	if qpm_capabilities != -1:
		properties.setdefault("qpm_capabilities", qpm_capabilities)
	service_id = service_id or \
		f"{service_name}:{endpoint_record['hostname']}:{endpoint_record['node_name']}"
	api_bindings = advertisement.get("api_bindings")
	if not api_bindings:
		raise ValueError("QPM service metadata missing api_bindings")
	return {
		"service_id": service_id,
		"service_name": service_name,
		"service_type": advertisement.get(
			"service_type", properties.get("service_type", "defw.service")),
		"runtime_id": (
			properties.get("runtime_id") or
			endpoint_record["runtime_id"]
		),
		"peer_handle": (
			properties.get("peer_handle") or
			_endpoint_attr(endpoint, "blk_uuid", ZERO_UUID)
		),
		"endpoint": endpoint_record,
		"api_bindings": [dict(binding) for binding in api_bindings],
		"selector": dict(
			advertisement.get("selector") or
			properties.get("selector") or
			{"resources": [service_name]}),
		"properties": properties,
		"capability": dict(advertisement.get("capability") or {}),
		"qpm_type": qpm_type,
		"qpm_capabilities": qpm_capabilities,
	}


def _site_dirsvc_client(defw_module, endpoint):
	connect_to_binding = getattr(defw_module, "connect_to_binding", None)
	if connect_to_binding is None:
		raise AttributeError(
			"DEFw runtime does not expose connect_to_binding")
	try:
		return connect_to_binding(_directory_binding_record(endpoint))
	except Exception:
		return None


def _register_site_record(client, record, defw_module):
	register_service = getattr(client, "register_service", None)
	if register_service is None:
		raise AttributeError(
			"site dirsvc client does not expose register_service")
	service_ep = _site_registration_service_endpoint(defw_module)
	if service_ep is None:
		raise AttributeError(
			"QPM DEFw endpoint unavailable for site registration")
	return register_service(
		service_ep,
		context=_site_registration_context(record),
	)


def _site_registration_context(record):
	return {
		"service_id": record.get("service_id"),
		"service_name": record.get("service_name"),
		"service_type": record.get("service_type"),
		"api_bindings": record.get("api_bindings"),
		"selector": record.get("selector"),
		"properties": record.get("properties"),
		"capability": record.get("capability"),
		"qpm_type": record.get("qpm_type"),
		"qpm_capabilities": record.get("qpm_capabilities"),
	}


def _site_registration_service_endpoint(defw_module):
	endpoint = _call_or_read(defw_module, "qpm_site_registration_endpoint")
	if _is_defw_service_endpoint(endpoint):
		return endpoint
	endpoint = _defw_endpoint(defw_module)
	if _is_defw_service_endpoint(endpoint):
		return endpoint
	return None


def _is_defw_service_endpoint(endpoint):
	return endpoint is not None and hasattr(endpoint, "get_id")


def _install_defw_directory_lifecycle_hook():
	try:
		import defw_directory as directory_module
	except Exception:
		return False
	add_listener = getattr(
		directory_module, "add_lifecycle_listener", None)
	if add_listener is None:
		return False
	try:
		add_listener(_record_defw_directory_lifecycle_event)
	except Exception:
		logging.exception(
			"failed to install DEFw directory lifecycle telemetry hook")
		return False
	return True


def _record_defw_directory_lifecycle_event(
		event_type, service_record=None, peer_event=None, reason=None,
		details=None):
	controller = _controller_for_service_record(service_record or {})
	if controller is None:
		return
	controller.record_defw_directory_event(
		event_type, service_record=service_record,
		peer_event=peer_event, reason=reason, details=details)


def _record_site_registration_lifecycle(record, registered_record, peer,
					directory_endpoint):
	service_record = dict(record)
	if isinstance(registered_record, dict):
		service_record.update(registered_record)
	if peer:
		service_record.setdefault("runtime_id", peer.get("runtime_id"))
		service_record.setdefault("peer_handle", peer.get("peer_handle"))
	_record_defw_directory_lifecycle_event(
		"registration", service_record=service_record,
		details={"directory_endpoint": directory_endpoint})


def _controller_for_service_record(record):
	properties = record.get("properties") or {}
	if not isinstance(properties, dict):
		return None
	controller_telemetry = properties.get("controller") or {}
	if not isinstance(controller_telemetry, dict):
		return None
	target_id = controller_telemetry.get("target_id")
	if target_id is None:
		return None
	return find_target_controller(target_id)


def _site_registration_peer(defw_module):
	peer = _call_or_read(defw_module, "qpm_site_registration_peer")
	if peer:
		return dict(peer)
	endpoint = _defw_endpoint(defw_module)
	endpoint_record = _endpoint_record(endpoint)
	return {
		"runtime_id": endpoint_record["runtime_id"],
		"peer_handle": _endpoint_attr(endpoint, "blk_uuid", ZERO_UUID),
		"endpoint": endpoint_record,
	}


def _directory_binding_record(endpoint):
	endpoint_record = _endpoint_record(endpoint)
	return {
		"service_record": {
			"service_id": f"dirsvc:{endpoint}",
			"service_name": "DEFwDirSvc",
			"service_type": "defw.dirsvc",
			"runtime_id": endpoint_record["runtime_id"],
			"endpoint": endpoint_record,
			"selector": {
				"resources": ["DEFwDirSvc"],
				"aliases": ["dirsvc", "directory"],
			},
			"properties": {},
		},
		"selected_binding": {
			"binding_name": "directory",
			"client_module": "api_dirsvc",
			"client_class": "DEFwDirSvc",
			"service_module": "svc_dirsvc.svc_dirsvc",
			"service_class": "DEFwDirSvc",
			"version": 1,
		},
	}


def _defw_endpoint(defw_module):
	runtime = getattr(defw_module, "me", None)
	if runtime is None or not hasattr(runtime, "my_endpoint"):
		raise RuntimeError("DEFw runtime does not expose me.my_endpoint()")
	return runtime.my_endpoint()


def _endpoint_record(endpoint):
	if isinstance(endpoint, dict):
		address = endpoint.get("address") or endpoint.get("addr") or \
			endpoint.get("host") or endpoint.get("hostname")
		listen_port = endpoint.get("listen_port") or endpoint.get("port") or 0
		return {
			"address": str(address or "localhost"),
			"listen_port": int(listen_port),
			"pid": int(endpoint.get("pid", 0) or 0),
			"node_name": str(endpoint.get("node_name") or
					 endpoint.get("name") or address or "localhost"),
			"hostname": str(endpoint.get("hostname") or address or "localhost"),
			"runtime_id": (
				endpoint.get("runtime_id") or
				endpoint.get("remote_uuid") or
				ZERO_UUID
			),
		}
	if isinstance(endpoint, str):
		address, listen_port = _parse_endpoint(endpoint)
		return {
			"address": address,
			"listen_port": listen_port,
			"pid": 0,
			"node_name": address,
			"hostname": address,
			"runtime_id": ZERO_UUID,
		}
	return {
		"address": str(_endpoint_attr(endpoint, "addr", "localhost")),
		"listen_port": int(_endpoint_attr(endpoint, "listen_port", 0) or 0),
		"pid": int(_endpoint_attr(endpoint, "pid", 0) or 0),
		"node_name": str(_endpoint_attr(endpoint, "name", "localhost")),
		"hostname": str(_endpoint_attr(endpoint, "hostname", "localhost")),
		"runtime_id": _endpoint_attr(endpoint, "remote_uuid", ZERO_UUID),
	}


def _endpoint_attr(endpoint, attr_name, default=None):
	value = getattr(endpoint, attr_name, default)
	return value() if callable(value) else value


def _parse_endpoint(endpoint):
	value = endpoint.strip()
	if "://" in value:
		from urllib.parse import urlparse

		parsed = urlparse(value)
		return parsed.hostname or "localhost", parsed.port or 0
	if value.count(":") == 1:
		host, port = value.rsplit(":", 1)
		try:
			return host, int(port)
		except ValueError:
			return host, 0
	return value or "localhost", 0


def _as_list(value):
	if value is None:
		return []
	if isinstance(value, list):
		return value
	if isinstance(value, tuple):
		return list(value)
	return [value]


def _site_dirsvc_endpoints():
	return [
		item.strip()
		for item in os.environ.get(SITE_DIRSVC_ENDPOINTS_ENV, "").replace(
			";", ",").split(",")
		if item.strip()
	]


def complete_qpm_initialization(message):
	uq.qpm_initialized = True
	_write_service_ready(message)
	logging.debug(message)


def _write_service_ready(message):
	path = os.environ.get(SERVICE_READY_FILE_ENV)
	if not path:
		return
	ready_file = Path(path)
	try:
		ready_file.parent.mkdir(parents=True, exist_ok=True)
		with ready_file.open("w", encoding="utf-8") as stream:
			json.dump({
				"ready": True,
				"message": message,
				"timestamp_ns": time_ns(),
			}, stream, sort_keys=True)
			stream.write("\n")
	except OSError:
		logging.exception("failed to write QPM service readiness file")


def wait_for_dirsvc(defw_module, message, timeout=None):
	deadline = None
	if timeout is not None and timeout >= 0:
		deadline = monotonic() + timeout
	while not _dirsvc_ready(defw_module) and not uq.qpm_shutdown:
		if deadline is not None and monotonic() >= deadline:
			logging.error("timed out waiting for QPM directory service")
			return
		logging.debug("still waiting for dirsvc to come up")
		sleep(1)
	if not uq.qpm_shutdown:
		complete_qpm_initialization(message)


def wait_for_startup(defw_module, message, timeout=None):
	deadline = None
	if timeout is not None and timeout >= 0:
		deadline = monotonic() + timeout
	while _startup_wait_reason(defw_module) and not uq.qpm_shutdown:
		if deadline is not None and monotonic() >= deadline:
			logging.error("timed out waiting for QPM startup readiness")
			return
		logging.debug("still waiting for QPM startup readiness")
		sleep(1)
	if not uq.qpm_shutdown:
		complete_qpm_initialization(message)


def _start_wait_thread(defw_module, message, timeout=None):
	thread = threading.Thread(target=wait_for_startup,
				  args=(defw_module, message, timeout))
	thread.daemon = True
	thread.start()


def initialize_qpm_service(defw_module, message):
	if uq.qpm_initialized:
		return "already-initialized"

	_install_defw_directory_lifecycle_hook()
	timeout = startup_timeout()
	wait_reason = _startup_wait_reason(defw_module)
	if wait_reason is not None:
		_start_wait_thread(defw_module, message, timeout=timeout)
		return f"waiting-for-{wait_reason}"

	complete_qpm_initialization(message)
	return "initialized"


def uninitialize_qpm_service(message):
	uq.qpm_shutdown = True
	logging.debug(message)
