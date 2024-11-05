import defw, logging
from defw_app_util import defw_get_resource_mgr, defw_reserve_service_by_name
from time import sleep
from defw_exception import DEFwNotReady

qpm_api = None
system_up_timeout = 40

def test_qpm(qpm_api):
	logging.debug("Testing QPM")
	logging.debug(qpm_api.test())

def get_qpm():
	global qpm_api

	if qpm_api:
		return qpm_api

	#Grab a qpm if one exists
	rmgr = defw_get_resource_mgr()
	qpm_api = defw_reserve_service_by_name(rmgr, 'QPM')[0]

	logging.debug(f"got the qpm {qpm_api}")

	try:
		test_qpm(qpm_api)
	except Exception as e:
		logging.debug(f"QPM ran into an exception {e}")
		qpm_api.shutdown()

	return qpm_api


