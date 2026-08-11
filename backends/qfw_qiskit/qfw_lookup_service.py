import logging
import os
import inspect

import defw
from defw_app_util import defw_get_directory_service, SYSTEM_UP_TIMEOUT
from .qpm_resolver import (
	DIRECT_ENDPOINT_FALLBACK_ENV,
	DIRECT_QPM_ENDPOINT_ENV,
	QPM_IMPL_ENV,
	QPMResolver,
	SITE_DIRSVC_ENDPOINTS_ENV,
)


def _connect_qpm(dirsvc, qpm_type, qpm_capabilities,
		 timeout=SYSTEM_UP_TIMEOUT, provider=None):
	want = provider or os.environ.get(QPM_IMPL_ENV)
	resolver = QPMResolver.from_environment(dirsvc=dirsvc, defw_module=defw)
	request = {
		"timeout": timeout,
		"binding_name": "default",
		"qpm_type": qpm_type,
		"qpm_capabilities": qpm_capabilities,
	}
	if want:
		request["provider"] = want
	return resolver.connect(**request)


def _external_qpm_resolution_configured():
	if os.environ.get(SITE_DIRSVC_ENDPOINTS_ENV):
		return True
	value = os.environ.get(DIRECT_ENDPOINT_FALLBACK_ENV, "")
	if value.strip().lower() not in {"1", "true", "yes", "on", "y"}:
		return False
	return bool(os.environ.get(DIRECT_QPM_ENDPOINT_ENV))


def _embedded_directory_expected():
	value = os.environ.get("DEFW_DISABLE_DIRSVC", "")
	if value.strip().lower() in {"0", "false", "no", "off", "n"}:
		return True
	return bool(
		os.environ.get("DEFW_PARENT_HOSTNAME") and
		os.environ.get("DEFW_PARENT_PORT"))


def _get_directory_service(timeout):
	signature = inspect.signature(defw_get_directory_service)
	if (
			"timeout" in signature.parameters or
			any(
				item.kind == item.VAR_KEYWORD
				for item in signature.parameters.values())):
		return defw_get_directory_service(timeout=timeout)
	return defw_get_directory_service()


def _optional_directory_service(timeout=SYSTEM_UP_TIMEOUT):
	try:
		return _get_directory_service(timeout)
	except Exception:
		if _embedded_directory_expected():
			raise
		if _external_qpm_resolution_configured():
			return None
		raise


def test_qpm(qpm_api):
	logging.debug("Testing QPM")
	logging.debug(qpm_api.test())


def get_qpm(qpm_type=-1, qpm_capabilities=-1, timeout=SYSTEM_UP_TIMEOUT,
	    provider=None):
	# Grab a qpm if one exists.
	dirsvc = _optional_directory_service(timeout=timeout)
	qpm_api = _connect_qpm(
		dirsvc,
		qpm_type,
		qpm_capabilities,
		timeout=timeout,
		provider=provider)

	logging.debug(f"got the qpm {qpm_api}")

	try:
		test_qpm(qpm_api)
	except Exception as e:
		logging.debug(f"QPM ran into an exception {e}")
		shutdown = getattr(qpm_api, "shutdown", None)
		if shutdown:
			shutdown()

	return qpm_api
