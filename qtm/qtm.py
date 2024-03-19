# This script is designed to run via the DEFw framework
import defw, logging, yaml
from defw_exception import DEFwCommError
from time import sleep
import supermarq, random

def get_first_qpm():
	wait = 0
	while wait < 40:
		res = defw.resmgr.get_services('QPM')
		if res and len(res) > 0:
			break
		wait += 1
		logging.debug("Waiting to connect to a QPM")
		sleep(1)

	if len(res) == 0:
		raise DEFwCommError("Couldn't connect to a QPM")

	logging.debug(f"-------> Found QPM: {res}")
	qpm_res = {}
	for k, v in res.items():
		qpm_res[k] = v
		break

	ep = defw.resmgr.reserve(defw.me.my_endpoint(), qpm_res)
	logging.debug(f"connect to QPM {ep}")
	defw.connect_to_services(ep)
	class_obj = getattr(defw.service_apis['QPM'],
						res[list(res.keys())[0]]['api'])
	qpm_api = class_obj(ep[0])

	return qpm_api

def run_circuit(api):
#	phasecode_list = [random.randint(0, 1) for _ in range(40)]
#	c = supermarq.benchmarks.phase_code.PhaseCode(40, 1, phasecode_list)
#	cir = c.circuit()
#	qasm = cir.to_qasm()
	ghz = supermarq.benchmarks.ghz.GHZ(num_qubits=3)
	cir = ghz.circuit()
	qasm = cir.to_qasm()

	info = {}
	info['qasm'] = qasm
	info['num_qubits'] = 3
	info['num_shots'] = 1
	info['compiler'] = 'staq'
	for x in range(0, 10):
		try:
			cid = api.create_circuit(info)
			rc, output = api.sync_run(cid)
			logging.debug(f"type(output) = {type(output)}")
			if type(output) == bytes:
				logging.debug(output.decode('utf-8'))
			else:
				logging.debug(output)
		except Exception as e:
			logging.critical(f"Got an exception {e} of type: {type(e)}")
			logging.critical(e)

def run_circuit2(api):
	for x in range(3, 20):
		ghz = supermarq.benchmarks.ghz.GHZ(num_qubits=x)
		cir = ghz.circuit()
		qasm = cir.to_qasm()

		info = {}
		info['qasm'] = qasm
		info['num_qubits'] = x
		info['num_shots'] = 1
		info['compiler'] = 'staq'
		try:
			cid = api.create_circuit(info)
			#rc, output = api.sync_run(cid)
			#print(f"{output.decode('utf-8')}")
			rc, circ_result, stats = api.sync_run(cid)
			logging.debug(yaml.dump(circ_result, sort_keys=False))
			for s in stats:
				logging.debug(yaml.dump(s, sort_keys=False))
		except Exception as e:
			logging.critical(f"Got an exception {e} of type: {type(e)}")
			logging.critical(e)

# This will throw an exception if there is a problem
def test_qpm(qpm_api):
	logging.debug("Testing QPM")
	logging.debug(qpm_api.test())

if __name__ == "__main__":
	wait = 0
	while wait < 20:
		resmgr = defw.get_resmgr()
		if resmgr:
			break
		wait += 1
		print("waiting for resmgr")
		sleep(1)

	if resmgr:
		logging.debug(f"found a resmgr {resmgr}")
	else:
		logging.debug("Couldn't find a resmgr")

	# Grab a qpm if one exists
	qpm = get_first_qpm()

	wait = 0
	while wait < 20:
		if qpm.is_ready():
			break
		logging.debug("QPM not ready yet")
		wait += 1
		sleep(1)

	try:
		test_qpm(qpm)

		run_circuit2(qpm)
		qpm.shutdown()
	except Exception as e:
		logging.debug(f"QTM ran into an exception {e}")
		qpm.shutdown()

	# initialize MPI4PY
	# Now ready to receive MPI messages
