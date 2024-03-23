# This script is designed to run via the DEFw framework
import defw, logging, yaml
from defw_exception import DEFwCommError, DEFwInProgress, DEFwNotReady
from defw_util import prformat, fg, bg
from time import sleep
import supermarq, random

req_timeout = 20
system_up_timeout = 40
circuit_run_timeout = 100

def get_first_qpm():
	global system_up_timeout

	wait = 0
	while wait < system_up_timeout:
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

def async_run_circuit2(api, itr=30):
	global circuit_run_timeout

	start_qubits = 20
	for x in range(0, itr):
		ghz = supermarq.benchmarks.ghz.GHZ(num_qubits=start_qubits)
		cir = ghz.circuit()
		qasm = cir.to_qasm()

		start_qubits += 1

		wait = 0
		info = {}
		info['qasm'] = qasm
		info['num_qubits'] = start_qubits
		info['num_shots'] = 1
		info['compiler'] = 'staq'
		try:
			cid = api.create_circuit(info)
			api.async_run(cid)
		except Exception as e:
			logging.critical(f"Got an exception {e} of type: {type(e)}")
			logging.critical(e)
			raise e

	total_circuits_completed = 0
	while (wait < circuit_run_timeout and total_circuits_completed != itr):
		try:
			r = api.read_cq()
			prformat(fg.green+fg.bold, f"finished with result {r['cid']}:")
			prformat(fg.green+fg.bold, f"{r['result'].decode('utf-8')}")
			total_circuits_completed += 1
		except Exception as e:
			if type(e) == DEFwInProgress:
				prformat(fg.red+fg.bold, f"{cid} has not completed yet")
				wait += 1
				sleep(1)
				continue
			else:
				raise e

def async_run_circuit(api, num_qubits):
	global circuit_run_timeout

	ghz = supermarq.benchmarks.ghz.GHZ(num_qubits=num_qubits)
	cir = ghz.circuit()
	qasm = cir.to_qasm()

	wait = 0
	info = {}
	info['qasm'] = qasm
	info['num_qubits'] = num_qubits
	info['num_shots'] = 1
	info['compiler'] = 'staq'
	try:
		cid = api.create_circuit(info)
		api.async_run(cid)
		while wait < circuit_run_timeout:
			try:
				r = api.read_cq(cid)
				prformat(fg.green+fg.bold, f"{cid} finished with result {r}")
				break
			except Exception as e:
				if type(e) == DEFwInProgress:
					prformat(fg.red+fg.bold, f"{cid} has not completed yet")
					wait += 1
					sleep(1)
					continue
				else:
					raise e
	except Exception as e:
		logging.critical(f"Got an exception {e} of type: {type(e)}")
		logging.critical(e)

def run_circuit(api, num_qubits):
#	phasecode_list = [random.randint(0, 1) for _ in range(40)]
#	c = supermarq.benchmarks.phase_code.PhaseCode(40, 1, phasecode_list)
#	cir = c.circuit()
#	qasm = cir.to_qasm()
	ghz = supermarq.benchmarks.ghz.GHZ(num_qubits=num_qubits)
	cir = ghz.circuit()
	qasm = cir.to_qasm()

	info = {}
	info['qasm'] = qasm
	info['num_qubits'] = num_qubits
	info['num_shots'] = 1
	info['compiler'] = 'staq'
	try:
		cid = api.create_circuit(info)
		rc, output, stats = api.sync_run(cid)
		logging.debug(f"type(output) = {type(output)}")
		if type(output) == bytes:
			logging.debug(f"output: {output.decode('utf-8')}")
		else:
			logging.debug(f"output: {output}")
	except Exception as e:
		logging.critical(f"Got an exception {e} of type: {type(e)}")
		logging.critical(e)

def run_circuit2(api, start, end):
	for x in range(start, end):
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
	req_timeout = 20
	circuit_run_timeout = 100
	system_up_timeout = 40

	if len(sys.argv) >= 2:
		argv = sys.argv[1:]

		try:
			options, args = getopt.getopt(argv, "u:c:t:h",
			["system-up-timeout=", "circuit-run-timeout=", "timeout=", "help"])
		except:
			prformat(fg.red+fg.bold, f"bad command line arguments")
			me.exit()

		for name, value in options:
				if name in ['-u', '--system-up-timeout']:
					system_up_timeout = int(value)
				if name in ['-c', '--circuit-run-timeout']:
					circuit_run_timeout = int(value)
				if name in ['-t', '--timeout']:
					req_timeout = int(value)
				else:
					prformat(fg.red+fg.bold, f"Unknown parameters {name}:{value}")
					me.exit()

	wait = 0
	while wait < system_up_timeout:
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
	while wait < system_up_timeout:
		try:
			qpm.is_ready()
			break
		except Exception as e:
			if type(e) == DEFwNotReady:
				logging.debug("QPM not ready yet")
				wait += 1
				sleep(1)
			else:
				raise e

	try:
		test_qpm(qpm)

		async_run_circuit2(qpm, itr=3)
		#run_circuit(qpm, 20)
		#run_circuit2(qpm, 3, 10)
		qpm.shutdown()
	except Exception as e:
		logging.debug(f"QTM ran into an exception {e}")
		qpm.shutdown()

	# initialize MPI4PY
	# Now ready to receive MPI messages
