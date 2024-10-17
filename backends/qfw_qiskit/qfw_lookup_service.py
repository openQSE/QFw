import defw, logging
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

	qpm_api = defw.get_first_service('QPM', system_up_timeout)

	wait = 0
	while wait < system_up_timeout:
		try:
			qpm_api.is_ready()
			break
		except Exception as e:
			if type(e) == DEFwNotReady:
				logging.debug("QPM not ready yet")
				wait += 1
				sleep(1)
			else:
				raise e

	try:
		test_qpm(qpm_api)
	except Exception as e:
		logging.debug(f"QTM ran into an exception {e}")
		qpm_api.shutdown()


