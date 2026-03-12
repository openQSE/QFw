from defw_exception import DEFwCommError, DEFwAgentNotFound
from defw_agent_baseapi import query_service_info
from defw_util import expand_host_list
from .svc_qpm import QPM
import cdefw_global
import defw
import sys, os, threading, logging
from time import sleep
import util.qpm.util_qpm as uq

SERVICE_NAME = 'QPM'
SERVICE_DESC = 'Quantum Platform Manager for Quantum Brilliance'

# This is used by the infrastructure to display information about
# the service module. The name is also used as a key through out the
# infrastructure. Without it the service module will not load.
svc_info = {'name': SERVICE_NAME,
			'module': __name__,
			'description': SERVICE_DESC,
			'version': 1.0}

# This is used by the infrastructure to define all the service classes.
# Each class should be a separate service. Each class should implement the
# following methods:
#	query()
#	reserve()
#	release()
service_classes = [QPM]

def qpm_complete_init():
	uq.qpm_initialized = True
	logging.debug("QPM Initialized Successfully")

def qpm_wait_resmgr():
	while not defw.resmgr and not uq.qpm_shutdown:
		logging.debug("still waiting for resmgr to come up")
		sleep(1)
	if not uq.qpm_shutdown:
		qpm_complete_init()

def initialize():
	global g_timeout

	if uq.qpm_initialized:
		return

	try:
		g_timeout = int(os.environ['QFW_STARTUP_TIMEOUT'])
	except:
		g_timeout = 40

	if not defw.resmgr:
		# we haven't connected to the resmgr yet. Spin up a thread and
		# wait for it to connect before finishing up the initialization
		svc_qpm_thr = threading.Thread(target=qpm_wait_resmgr, args=())
		svc_qpm_thr.daemon = True
		svc_qpm_thr.start()
		return

	qpm_complete_init()

def uninitialize():
	uq.qpm_shutdown = True

	logging.debug("QPM shutdown")
