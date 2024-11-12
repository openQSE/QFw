import defw, logging
from defw_app_util import defw_get_resource_mgr, defw_reserve_service_by_name
from time import sleep
from defw_exception import DEFwNotReady

def test_qpm(qpm_api):
	logging.debug("Testing QPM")
	logging.debug(qpm_api.test())

def get_qpm(qpm_type=-1, qpm_cap=-1):
	#Grab a qpm if one exists
	rmgr = defw_get_resource_mgr()
	qpm_api = defw_reserve_service_by_name(rmgr, 'QPM', qpm_type, qpm_cap)[0]

	logging.debug(f"got the qpm {qpm_api}")

	try:
		test_qpm(qpm_api)
	except Exception as e:
		logging.debug(f"QPM ran into an exception {e}")
		qpm_api.shutdown()

	return qpm_api


