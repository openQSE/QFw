import logging
import os

import defw
from defw_app_util import defw_get_resource_mgr, SYSTEM_UP_TIMEOUT
from .qpm_resolver import (
	DIRECT_ENDPOINT_FALLBACK_ENV,
	DIRECT_QPM_ENDPOINT_ENV,
	QPM_IMPL_ENV,
	QPMResolver,
	SITE_DIRSVC_ENDPOINTS_ENV,
)


def _reserve_qpm(rmgr, qpm_type, qpm_cap, timeout=SYSTEM_UP_TIMEOUT):
	want = os.environ.get(QPM_IMPL_ENV)
	resolver = QPMResolver.from_environment(rmgr=rmgr, defw_module=defw)
	request = {
		"timeout": timeout,
		"qpm_type": qpm_type,
		"qpm_capability": qpm_cap,
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


def _optional_resource_manager():
	try:
		return defw_get_resource_mgr()
	except Exception:
		if _external_qpm_resolution_configured():
			return None
		raise


def test_qpm(qpm_api):
	logging.debug("Testing QPM")
	logging.debug(qpm_api.test())


def get_qpm(qpm_type=-1, qpm_cap=-1):
	# Grab a qpm if one exists.
	rmgr = _optional_resource_manager()
	qpm_api = _reserve_qpm(rmgr, qpm_type, qpm_cap)

	logging.debug(f"got the qpm {qpm_api}")

	try:
		test_qpm(qpm_api)
	except Exception as e:
		logging.debug(f"QPM ran into an exception {e}")
		qpm_api.shutdown()

	return qpm_api
