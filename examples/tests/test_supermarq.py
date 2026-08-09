# This script is designed to run via the DEFw framework
import logging
import yaml
import threading
import select
import traceback
import sys
import os
import supermarq
import getopt
from defw import me
from defw_exception import DEFwInProgress, DEFwNotReady, DEFwError
from defw_util import prformat, fg
from defw_app_util import defw_get_directory_service, defw_bind_service_by_name
from time import sleep, time
from defw_event_baseapi import BaseEventAPI
from qfw_example_report import emit_result, parse_bool

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


def async_wait_read_cq(api, total_circ, reservation_id):
	total_circuits_completed = 0
	start = time()
	while time() - start < circuit_run_timeout and total_circuits_completed != total_circ:
		try:
			r = api.read_cq(reservation_id=reservation_id)
			if isinstance(r, dict) and not r.get("completion_ready", True):
				prformat(fg.red + fg.bold, "waiting on circuit completion")
				sleep(1)
				continue
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
	return total_circuits_completed


def result_reader(total_circ, event_api, result_state):
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
	result_state["completed"] = total_circuits_completed
	result_state["results"] = results


EVENT_TYPE_CIRC_RESULT = 1


def build_circuit_plan(cb, start_qubits, num_shots, itr, increase):
	circuit_plan = []
	nqubits = start_qubits
	for iteration in range(0, itr):
		for qasm in cb(nqubits, 1):
			circuit_plan.append({
				"iteration": iteration,
				"num_qubits": nqubits,
				"info": {
					"qasm": qasm,
					"num_qubits": nqubits,
					"num_shots": num_shots,
					"compiler": "staq",
				},
			})
		if increase:
			nqubits += 1
	return circuit_plan


def configure_admission_policy(qpm):
	result = qpm.set_admission_policy({"name": "unlimited"})
	logging.defw_app(f"set_admission_policy: {yaml.dump(result)}")
	return result


def reserve_execution(qpm, circuit_plan, backend, runtype, method_name,
		      iterations, startqbit, num_shots, increase):
	if not circuit_plan:
		raise DEFwError("reservation requires at least one circuit")

	configure_admission_policy(qpm)
	operation = "sync_run" if runtype == "sync" else "async_run"
	job_id = os.environ.get("SLURM_JOB_ID", "supermarq")
	max_qubits = max(record["num_qubits"] for record in circuit_plan)
	request = {
		"owner": {"user": os.environ.get("USER", "supermarq")},
		"job_id": job_id,
		"allocation_id": job_id,
		"num_qubits": max_qubits,
		"walltime_ns": max(1, circuit_run_timeout) * 1_000_000_000,
		"ttl_ns": max(60, system_up_timeout + circuit_run_timeout + 30) *
			1_000_000_000,
		"workload": {
			"example": "qfw_supermarq",
			"operation": operation,
			"backend": backend or "tnqvm",
			"method": method_name,
		},
		"run_context": {"operation": operation},
		"task_class": {
			"count": len(circuit_plan),
			"qubit_count": max_qubits,
			"shots": num_shots,
			"measurement_count": max_qubits,
		},
		"parameters": {
			"iterations": iterations,
			"startqbit": startqbit,
			"increase": increase,
		},
	}
	decision = qpm.reserve(request=request)
	logging.defw_app(f"reserve: {yaml.dump(decision)}")
	if decision.get("status") != "accepted" or not decision.get(
			"reservation_id"):
		raise DEFwError(f"reservation was not accepted: {decision}")
	return decision["reservation_id"]


def release_execution(qpm, reservation_id):
	if reservation_id is None:
		return None
	result = qpm.release(reservation_id=reservation_id, reason=0)
	logging.defw_app(f"release: {yaml.dump(result)}")
	return result


def async_run_circuit(api, circuit_plan, reservation_id, read_cq=True):
	start_time = time()

	logging.defw_app(f"Application start: {start_time}")

	total_circ = len(circuit_plan)

	runner = None
	event_api = None
	result_state = {"completed": 0, "results": []}
	if not read_cq:
		event_api = BaseEventAPI()
		event_api.register_external()
		logging.defw_app(f"Registering Event: {time()}")
		api.register_event_notification(
			me.my_endpoint(), EVENT_TYPE_CIRC_RESULT, event_api.class_id(),
			reservation_id=reservation_id)
		runner = threading.Thread(
			target=result_reader, args=(total_circ, event_api, result_state,))
		runner.start()

	for record in circuit_plan:
		try:
			api.async_run(record["info"], reservation_id=reservation_id)
		except Exception as e:
			logging.defw_app(f"Got an exception {e} of type: {type(e)}")
			logging.defw_app(e)
			raise e

	if read_cq:
		completed = async_wait_read_cq(api, total_circ, reservation_id)
	else:
		runner.join()
		completed = result_state["completed"]

	if completed != total_circ:
		raise DEFwError(
			f"only received {completed} of {total_circ} circuit completions")

	logging.defw_app(f'thread joined at {time()}')

	duration = time() - start_time
	max_qubits = max(record["num_qubits"] for record in circuit_plan)
	prformat(fg.orange + fg.bold,
		 f"****{total_circ} {max_qubits} qubit circuits completed in {duration}")
	return {
		"mode": "async",
		"submitted_circuits": total_circ,
		"completed_circuits": completed,
		"duration_sec": duration,
		"read_cq": read_cq,
	}


def run_circuit(api, circuit_plan, reservation_id):
	records = []
	start_time = time()
	for record in circuit_plan:
		try:
			circ_result = api.sync_run(
				record["info"], reservation_id=reservation_id)
			records.append({
				"iteration": record["iteration"],
				"num_qubits": record["num_qubits"],
				"result": circ_result,
			})
			logging.debug(yaml.dump(circ_result, sort_keys=False))
			prformat(fg.green + fg.bold, yaml.dump(circ_result, sort_keys=False))
		except Exception as e:
			logging.defw_app(f"Got an exception {e} of type: {type(e)}")
			logging.defw_app(e)
			raise e
	return {
		"mode": "sync",
		"iterations": records,
		"duration_sec": time() - start_time,
	}


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
				increase = parse_bool(value)
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

	dirsvc = defw_get_directory_service()
	qpm = defw_bind_service_by_name(dirsvc, 'QPM', svc_type=svc_type)[0]

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
			exit_rc = 0
			reservation_id = None
			test_qpm(qpm)
			method_name = getattr(op, "__name__", str(op))
			circuit_plan = build_circuit_plan(
				op, startqbit, num_shots, iterations, increase)
			reservation_id = reserve_execution(
				qpm, circuit_plan, backend or "tnqvm", runtype, method_name,
				iterations, startqbit, num_shots, increase)

			if runtype == "sync":
				metrics = run_circuit(qpm, circuit_plan, reservation_id)
			elif runtype == "async":
				metrics = async_run_circuit(
					qpm, circuit_plan, reservation_id, read_cq=False)
			else:
				raise ValueError(f"Unknown run type {runtype}. Expect: async, sync")
			metrics["reservation_id"] = reservation_id
			emit_result(
				"supermarq",
				parameters={
					"run": runtype,
					"iterations": iterations,
					"startqbit": startqbit,
					"shots": num_shots,
					"increase": increase,
					"method": method_name,
					"backend": backend or "tnqvm",
				},
				metrics=metrics,
			)
		except Exception as e:
			logging.defw_app(f"QTM ran into an exception {e}")
			traceback.print_exc()
			exit_rc = 1
		finally:
			try:
				release_execution(qpm, reservation_id)
			except Exception as e:
				logging.defw_app(f"QPM release failed: {e}")
				traceback.print_exc()
			if parse_bool(os.environ.get("QFW_SUPERMARQ_SHUTDOWN_QPM", "yes")):
				qpm.shutdown()
		if exit_rc:
			sys.exit(exit_rc)
		me.exit()
