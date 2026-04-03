# This script is designed to run via the DEFw framework
import logging
import yaml
import threading
import select
import traceback
import sys
import supermarq
import getopt
from defw_exception import DEFwInProgress, DEFwNotReady, DEFwError
from defw_util import prformat, fg
from defw_app_util import me, defw_get_resource_mgr, defw_reserve_service_by_name
from time import sleep, time
from defw_event_baseapi import BaseEventAPI

req_timeout = 20
system_up_timeout = 40
circuit_run_timeout = 100


def create_vqe(start_qubits=2, itr=1):
	vqe = supermarq.vqe_proxy.VQEProxy(start_qubits, itr)
	qasm = []
	circuits = vqe.circuit()
	for circ in circuits:
		qasm.append(circ.to_qasm())
	return qasm


def create_ghz(start_qubits=2, itr=1):
	ghz = supermarq.benchmarks.ghz.GHZ(num_qubits=start_qubits)
	cir = ghz.circuit()
	qasm = cir.to_qasm()
	return [qasm]


def async_wait_read_cq(api, total_circ):
	total_circuits_completed = 0
	start = time()
	while time() - start < circuit_run_timeout and total_circuits_completed != total_circ:
		try:
			r = api.read_cq()
			prformat(fg.green + fg.bold, f"finished {r['cid']}:")
			prformat(fg.green + fg.bold, f"{yaml.dump(r['result'])}")
			#prformat(fg.green+fg.bold, f"{r['result'].decode('utf-8')}")
			total_circuits_completed += 1
		except Exception as e:
			if isinstance(e, DEFwInProgress):
				prformat(fg.red + fg.bold, "waiting on circuit completion")
				sleep(1)
				continue
			else:
				raise e


def result_reader(total_circ, event_api):
	total_circuits_completed = 0
	results = []

	start = time()
	logging.defw_app(f"thread start: {start}")
	event_fd = event_api.fileno()
	while time() - start < circuit_run_timeout and total_circuits_completed != total_circ:
		readable, _, _ = select.select([event_fd], [], [], 1)
		if len(readable) > 0 and event_fd not in readable:
			raise DEFwError("Something wrong with select")
		if len(readable) > 0:
			r = event_api.get()
			results += r
			total_circuits_completed += len(r)

	logging.defw_app(
		f"Result reader thread ending. Events: {total_circuits_completed}."
		f" Expected: {total_circ}. Time: {time()}")
	for r in results:
		logging.defw_app(f"{yaml.dump(r.get_event())}")


EVENT_TYPE_CIRC_RESULT = 1


def async_run_circuit(api, cb, start_qubits=20, num_shots=1, itr=30, increase=True, read_cq=True):
	start_time = time()

	logging.defw_app(f"Application start: {start_time}")

	total_circ = itr

	qasm = []
	qubits = []

	for x in range(0, itr):
		circs = cb(start_qubits, 1)
		for i in range(0, len(circs)):
			qubits.append(start_qubits)
		qasm += circs

		if increase:
			start_qubits += 1

	runner = None
	event_api = None
	if not read_cq:
		event_api = BaseEventAPI()
		event_api.register_external()
		logging.defw_app(f"Registering Event: {time()}")
		api.register_event_notification(
			me.my_endpoint(), EVENT_TYPE_CIRC_RESULT, event_api.class_id())
		runner = threading.Thread(target=result_reader, args=(len(qasm), event_api,))
		runner.start()

	i = 0
	for q in qasm:
		info = {}
		info['qasm'] = q
		info['num_qubits'] = qubits[i]
		info['num_shots'] = num_shots
		info['compiler'] = 'staq'
		try:
			api.async_run(info)
		except Exception as e:
			logging.defw_app(f"Got an exception {e} of type: {type(e)}")
			logging.defw_app(e)
			raise e
		i += 1

	if read_cq:
		async_wait_read_cq(api, total_circ)
	else:
		runner.join()

	logging.defw_app(f'thread joined at {time()}')

	prformat(fg.orange + fg.bold, f"****{itr} {start_qubits} qubit circuits completed in {time() - start_time}")


def run_circuit(api, cb, start, itr, num_shots, increase):
	nqubits = start
	for x in range(0, itr):
		qasm = cb(nqubits, 1)

		for q in qasm:
			info = {}
			info['qasm'] = q
			info['num_qubits'] = x
			info['num_shots'] = num_shots
			info['compiler'] = 'staq'
			try:
				circ_result = api.sync_run(info)
				logging.debug(yaml.dump(circ_result, sort_keys=False))
				prformat(fg.green + fg.bold, yaml.dump(circ_result, sort_keys=False))
			except Exception as e:
				logging.defw_app(f"Got an exception {e} of type: {type(e)}")
				logging.defw_app(e)
				raise e
		nqubits += increase


# This will throw an exception if there is a problem
def test_qpm(qpm_api):
	logging.debug("Testing QPM")
	logging.debug(qpm_api.test())


if __name__ == "__main__":
	req_timeout = 20
	circuit_run_timeout = 100
	system_up_timeout = 40
	iterations = 1
	startqbit = 3
	increase = False
	runtype = "async"
	op = create_ghz
	backend = ''
	num_shots = 1

	print("Starting test")
	if len(sys.argv) >= 2:
		argv = sys.argv[1:]

		long_opts = [
			"backend=", "method=", "run=", "increase=", "startqbit=",
			"shots=", "iterations=", "system-up-timeout=",
			"circuit-run-timeout=", "timeout=", "help"
		]
		try:
			options, args = getopt.getopt(argv, "b:m:y:s:q:o:i:u:c:t:h", long_opts)
		except Exception:
			prformat(fg.red + fg.bold, f"bad command line arguments. argv={argv}")
			me.exit()

		for name, value in options:
			if name in ['-u', '--system-up-timeout']:
				system_up_timeout = int(value)
			elif name in ['-b', '--backend']:
				backend = value.lower()
			elif name in ['-c', '--circuit-run-timeout']:
				circuit_run_timeout = int(value)
			elif name in ['-t', '--timeout']:
				req_timeout = int(value)
			elif name in ['-i', '--iterations']:
				iterations = int(value)
			elif name in ['-q', '--startqbit']:
				startqbit = int(value)
			elif name in ['-o', '--shots']:
				num_shots = int(value)
			elif name in ['-s', '--increase']:
				increase = int(value)
			elif name in ['-y', '--run']:
				runtype = value.lower()
			elif name in ['-m', '--method']:
				operation = value.lower()
				if operation == "ghz":
					op = create_ghz
				elif operation == "vqe":
					op = create_vqe
				else:
					prformat(fg.red + fg.bold, f"Unknown operation {operation}")
					me.exit()
			else:
				prformat(fg.red + fg.bold, f"Unknown parameters {name}:{value}")
				me.exit()

	from api_qpm import QPMType
	# Grab a qpm if one exists
	if not backend or backend == "tnqvm":
		svc_type = QPMType.QPM_TYPE_TNQVM
	elif backend == 'nwqsim':
		svc_type = QPMType.QPM_TYPE_NWQSIM
	elif backend == 'qb':
		svc_type = QPMType.QPM_TYPE_QB
	else:
		raise DEFwError(f"Provided backend '{backend}' not supported")

	rmgr = defw_get_resource_mgr()
	qpm = defw_reserve_service_by_name(rmgr, 'QPM', svc_type=svc_type)[0]

	wait = 0
	while wait < system_up_timeout:
		try:
			qpm.is_ready()
			break
		except Exception as e:
			if isinstance(e, DEFwNotReady):
				logging.debug("QPM not ready yet")
				wait += 1
				sleep(1)
			else:
				raise e

	try:
		test_qpm(qpm)

		if runtype == "sync":
			run_circuit(qpm, op, startqbit, iterations, num_shots, increase)
		elif runtype == "async":
			async_run_circuit(
				qpm, op, start_qubits=startqbit, num_shots=num_shots,
				itr=iterations, increase=increase, read_cq=False)
		else:
			raise ValueError(f"Unknown run type {runtype}. Expect: async, sync")
		qpm.shutdown()
	except Exception as e:
		logging.defw_app(f"QTM ran into an exception {e}")
		traceback.print_exc()
		qpm.shutdown()
	me.exit()
