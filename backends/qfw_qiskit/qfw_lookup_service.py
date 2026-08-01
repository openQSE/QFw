import logging
import os

import defw
from defw_app_util import defw_get_resource_mgr, SYSTEM_UP_TIMEOUT
from .qpm_resolver import QPM_IMPL_ENV, QPMResolver


def _reserve_qpm(rmgr, qpm_type, qpm_cap, timeout=SYSTEM_UP_TIMEOUT):
	want = os.environ.get(QPM_IMPL_ENV)
	resolver = QPMResolver.from_resource_manager(rmgr, defw_module=defw)
	request = {
		"timeout": timeout,
		"qpm_type": qpm_type,
		"qpm_capability": qpm_cap,
	}
	if want:
		request["provider"] = want
	return resolver.connect(**request)


def test_qpm(qpm_api):
	logging.debug("Testing QPM")
	logging.debug(qpm_api.test())


def get_qpm(qpm_type=-1, qpm_cap=-1):
	#Grab a qpm if one exists
	rmgr = defw_get_resource_mgr()
	qpm_api = _reserve_qpm(rmgr, qpm_type, qpm_cap)

	logging.debug(f"got the qpm {qpm_api}")

	try:
		test_qpm(qpm_api)
	except Exception as e:
		logging.debug(f"QPM ran into an exception {e}")
		qpm_api.shutdown()

	return qpm_api
