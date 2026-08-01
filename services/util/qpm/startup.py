import logging
import os
import threading
from time import monotonic, sleep

import util.qpm.util_qpm as uq


OPERATION_MODE_ENV = "QFW_QPM_OPERATION_MODE"
REGISTER_WITH_DIRSVC_ENV = "QFW_QPM_REGISTER_WITH_DIRSVC"
DIRECT_ENDPOINT_FALLBACK_ENV = "QFW_QPM_DIRECT_ENDPOINT_FALLBACK"
SITE_DIRSVC_ENDPOINTS_ENV = "QFW_SITE_DIRSVC_ENDPOINTS"
STARTUP_TIMEOUT_ENV = "QFW_STARTUP_TIMEOUT"

DEFAULT_OPERATION_MODE = "qfw-managed"
LONG_RUNNING_OPERATION_MODE = "long-running"
DEFAULT_STARTUP_TIMEOUT = 40

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


def direct_endpoint_fallback_enabled():
	return bool(_env_flag(DIRECT_ENDPOINT_FALLBACK_ENV, False))


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
	if direct_endpoint_fallback_enabled():
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
		"direct_endpoint_fallback": direct_endpoint_fallback_enabled(),
		"site_dirsvc_endpoints": os.environ.get(SITE_DIRSVC_ENDPOINTS_ENV, ""),
	}


def should_wait_for_dirsvc(defw_module):
	if not register_with_dirsvc():
		return False
	return not _dirsvc_ready(defw_module)


def _dirsvc_ready(defw_module):
	if long_running_mode_enabled():
		if not site_dirsvc_endpoints_configured():
			return True
		return _site_dirsvc_ready(defw_module)
	return getattr(defw_module, "resmgr", None) is not None


def _site_dirsvc_ready(defw_module):
	endpoints = _site_dirsvc_endpoints()
	if not endpoints:
		return True
	return all(_site_dirsvc_endpoint_ready(defw_module, endpoint)
		for endpoint in endpoints)


def _site_dirsvc_endpoint_ready(defw_module, endpoint):
	for method_name in (
			"site_dirsvc_ready",
			"dirsvc_ready",
			"directory_ready"):
		method = getattr(defw_module, method_name, None)
		if method is None:
			continue
		try:
			return bool(method(endpoint))
		except TypeError:
			return bool(method())
	for attr_name in ("site_dirsvc", "dirsvc"):
		client = getattr(defw_module, attr_name, None)
		if client is not None:
			return True
	return False


def _site_dirsvc_endpoints():
	return [
		item.strip()
		for item in os.environ.get(SITE_DIRSVC_ENDPOINTS_ENV, "").replace(
			";", ",").split(",")
		if item.strip()
	]


def complete_qpm_initialization(message):
	uq.qpm_initialized = True
	logging.debug(message)


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


def _start_wait_thread(defw_module, message, timeout=None):
	thread = threading.Thread(target=wait_for_dirsvc,
				  args=(defw_module, message, timeout))
	thread.daemon = True
	thread.start()


def initialize_qpm_service(defw_module, message):
	if uq.qpm_initialized:
		return "already-initialized"

	timeout = startup_timeout()
	if should_wait_for_dirsvc(defw_module):
		_start_wait_thread(defw_module, message, timeout=timeout)
		return "waiting-for-dirsvc"

	complete_qpm_initialization(message)
	return "initialized"


def uninitialize_qpm_service(message):
	uq.qpm_shutdown = True
	logging.debug(message)
