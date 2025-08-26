import uuid, time, copy, logging, threading, yaml, sys, select
from collections import deque

from qiskit.providers import BackendV2, Options
from qiskit.providers import convert_to_target
from qiskit.result import Result
from qiskit.compiler import assemble
from qiskit.transpiler import Target

from qiskit.providers.models import BackendConfiguration, BackendProperties

from qiskit_aer.backends.name_mapping import NAME_MAPPING

from qiskit.assembler import disassemble
from qiskit import qasm2, QuantumCircuit
from qiskit import qobj as qobj_module

from .qfw_job import QFWJob
from defw_exception import DEFwError, DEFwNotReady, DEFwInProgress, DEFwNotFound, DEFwDumper
from defw import me
from .qfw_lookup_service import get_qpm
from enum import IntFlag
from api_qpm import QPMType, QPMCapability
from defw_event_baseapi import BaseEventAPI
from defw_common_def import g_rpc_metrics

# This is a mirror of QPMType and QPMCapability. And they always need to
# match. The point here is not to expose QPM specific information to the
# application as they should always be abstracted away by the backend.
QFwBackendType = IntFlag('QFwBackendType', {name.replace("QPM_", "QFW_"): member for name, member in QPMType.__members__.items()})
QFwBackendCapability = IntFlag('QFwBackendCapability', {name.replace("QPM_", "QFW_"): member for name, member in QPMCapability.__members__.items()})

# parts of this are inspired from https://github.com/pnnl/NWQ-Sim/blob/main/qiskit/qiskit_nwqsim_provider/nwqsim_simulator.py; Thank you Dr. Ang Li.
# and from https://github.com/Qiskit/qiskit-aer/blob/main/qiskit_aer/backends/aerbackend.py; Thank you Qiskit-Aer

class CircuitMetrics:
	def __init__(self, window_size=4096):
		self.lock = threading.Lock()
		self.window_size = window_size
		self.db = {}

	def add_timing_locked(self, send_time, recv_time, db):
		rtt = recv_time - send_time
		db['total'] += 1
		db['window'].append(rtt)
		window_len = len(db['window'])
		if window_len > 0:
			db['avg'] = sum(db['window']) / window_len
		if rtt > db['max']:
			db['max'] = rtt
		if rtt < db['min']:
			db['min'] = rtt

	def add_time(self, start_time, end_time, label):
		with self.lock:
			if label not in self.db:
				self.db[label] = {'window': deque(maxlen=self.window_size),
												 'avg': 0.0, 'min': sys.maxsize, 'max': 0.0,
												 'total': 0}
			self.add_timing_locked(start_time, end_time, self.db[label])

	def dump(self):
		import copy

		dbcopy = copy.deepcopy(self.db)
		for k, v in dbcopy.items():
			del(v['window'])
		logging.defw_app("QFw Backend statistics")
		logging.defw_app(yaml.dump(dbcopy,
						 Dumper=DEFwDumper, indent=2, sort_keys=False))

g_circ_metrics = CircuitMetrics()

EVENT_TYPE_CIRC_RESULT = 1

class QFWBackend(BackendV2):
	def __init__(self, betype=-1, capability=-1, target = None, properties = None):
		self.log_time = time.time()
		self.qpm = get_qpm(betype, capability)
		# register for events with the qpm
		self.event_api = BaseEventAPI()
		self.event_api.register_external()
		self.qpm.register_event_notification(me.my_endpoint(),
							EVENT_TYPE_CIRC_RESULT, self.event_api.class_id())

		super().__init__(name="QFw Backend")
		self._target = target
		self._properties = properties

		# Custom option values for config, properties
		self._options_configuration = {}
		self._options_properties = {}

		self.options.set_validator("shots", (1, 65536))
		# TODO: API to get supported gates at the backend.
		self._configuration_dict = {
			"backend_name": "qfw",
			"backend_version": "2.0",
			"n_qubits": 400,
			"basis_gates": ['x', 'y', 'z', 'h', 's', 'sdg', 't', 'tdg', 'ri', 'rx', 'ry', 'rz', 'sx', 'p', 'u', 'cx', 'cy', 'cz', 'ch', 'cs', 'csdg', 'ct', 'ctdg', 'crx', 'cry', 'crz', 'csx', 'cp', 'cu', 'id', 'swap', 'm', 'ma', 'reset', 'u1', 'u2', 'u3', 'ccx', 'cswap', 'rxx', 'ryy', 'rzz'],
			"coupling_map": None,
			"simulator": True,
			"local": True,
			"conditional": True,
			'open_pulse': False,
			"memory": True,
			"max_shots": 65536,
			"description": "Backend for using the quantum framework on frontier with qiskit!",
			"gates": []
		}

	def shutdown(self):
		g_circ_metrics.dump()
		self.qpm.shutdown()
		me.exit()

	def configuration(self):
		return BackendConfiguration.from_dict(self._configuration_dict)

	def properties(self):
		return BackendProperties.from_dict(self._options_properties)

	def get_memory_from_counts(self, counts):
		# return [] # uncomment when you don't need later!
		m = []
		for k,v in counts.items():
			for i in range(v):
				m.append(k)
		return m

	@property
	def target(self):
		# return Target("QFW Sim")
		if self._target is not None:
			return self._target
		# print("config - ", self._configuration_dict)
		tgt = convert_to_target(self.configuration(), None, None, NAME_MAPPING)
		return tgt

	@property
	def max_circuits(self):
		return 1024

	@classmethod
	def _default_options(cls):
		return Options(shots=1024, seed=334)

	def run_experiment_sync(self, circuit, experiment, options):
		self.start_time = time.time()

		# print("1 ", type(experiment))
		# print("2", dir(experiment))
		# print("3", experiment)
		# print("4", experiment.to_dict())

		# experiment_dict = experiment.to_dict()
		# experiment_dict['type'] = 'QASM'

		# qcobj_for_this_ex = qobj_module.QasmQobj.from_dict(experiment_dict)

		# print("5 ", qcobj_for_this_ex)
		# print("6 ", type(qcobj_for_this_ex))

		# circuit, run_config_out, headers = disassemble(qcobj_for_this_ex)

		# print("7 ", type(circuit))
		# print("8 ", dir(circuit))
		# print("9 ", circuit)

		# get qasm string from experiment
		qasm_string = qasm2.dumps(circuit)
		# get num_qubits from experiment
		num_qubits = circuit.num_qubits
		# let's form the info object to give to QFw!!
		info = {
			"qasm": qasm_string,
			"num_qubits": num_qubits,
			"num_shots": options["shots"],
			"compiler": "staq", # only for tnqvm, it is not used by nwqsim, TODO: need to think of a cleaner way..
		}
		try:
			cid = self.qpm.create_circuit(info)
			rc, output = self.qpm.sync_run(cid)
			if rc == 0:
				output = {"counts": output, "statevector": [], "memory": self.get_memory_from_counts(output)} # TODO: print statevector and memory (per shot) in nwqsim's executable/run,
				# output = {"counts": output, "statevector": []}
			else:
				output = {"counts": {}, "statevector": [], "memory": []}
				# output = {"counts": {}, "statevector": []}
		except Exception as e:
			print("Error! = ", str(e))
			output = {"Error": str(e), "counts": {"error": str(e)}, "statevector": [str(e)], "memory": []}
			# output = {"Error": str(e), "counts": {"error": str(e)}, "statevector": [str(e)]}

		self.end_time = time.time()

		result_dict = {
			"header": {
				"name": experiment.header.name,
				"memory_slots": experiment.config.memory_slots,
				"creg_sizes": experiment.header.creg_sizes,
			},
			"status": "DONE",
			"time_taken": self.end_time - self.start_time,
			"seed": options["seed"],
			"shots": options["shots"],
			"data": {"counts": output["counts"], "statevector": output["statevector"], "memory": output["memory"]},
			# "data": {"counts": output["counts"], "statevector": output["statevector"],},
			"success": True,
			"memory": True,
		}
		return result_dict

	# This is used by JobV1 and is upto the backend whether it is async or sync.
	# If want async, use job.submit() and .status() from the client/application.
	def run(self, circuits, run_async=True, **kwargs):
		for kwarg in kwargs:
			if not hasattr(self.options, kwarg):
				print("Option ", kwarg, " is not used by this backend")
		options = {
			"shots": kwargs.get("shots", self.options.shots),
			"seed": kwargs.get("seed", self.options.seed),
		}
		job_id = str(uuid.uuid4())
		# TODO: this can be used to submit batch, TODO: QFw level implement QPM API to accept batch async runs
		if run_async:
			return QFWJob(self, job_id, self._run_async_job, circuits, options)
		else:
			return QFWJob(self, job_id, self._run_sync_job, circuits, options)

	def _run_sync_job(self, job_id, circuits, options):
		if isinstance(circuits, qobj_module.QasmQobj):
			# print("\t\t of type QasmQobj")
			qobj = circuits
			# print("\t\t QFW does not work for qasmObj types as of now!!!!!!")
		elif isinstance(circuits, QuantumCircuit):
			# print("\t\t of type QuantumCircuit")
			circuits = [circuits]
			qobj = assemble(circuits)
		# elif isinstance(circuits, list(QuantumCircuit)):
		# 	# circuits = circuits
		else:
			# print("\t\t not of type QasmQobj")
			# print(type(circuits))
			qobj = assemble(circuits)

		result_list = []

		start = time.time()
		# for experiment in qobj.experiments:
		for (circuit,experiment) in zip(circuits, qobj.experiments):
			result_list.append(self.run_experiment_sync(circuit, experiment, options))
		end = time.time()

		# print("\n\n result_list = ", result_list, "\n\n")

		result = {
			"backend_name": self._configuration_dict["backend_name"],
			"backend_version": self._configuration_dict["backend_version"],
			"qobj_id": qobj.qobj_id,
			"job_id": job_id,
			"results": result_list,
			"status": "COMPLETED",
			"success": True,
			"time_taken": (end-start),
			"memory": True,
		}
		return Result.from_dict(result)

	# ASYNC	
	def run_experiment_async(self, circuit, experiment, options):
		self.start_time = time.time()
		# get qasm string from experiment
		qasm_string = qasm2.dumps(circuit)
		# get num_qubits from experiment
		num_qubits = circuit.num_qubits
		# let's form the info object to give to QFw!!
		info = {
			"qasm": qasm_string,
			"num_qubits": num_qubits,
			"num_shots": options["shots"],
			"compiler": "staq", # only for tnqvm, it is not used by nwqsim, TODO: need to think of a cleaner way..
		}
		try:
			cid = self.qpm.async_run(info)
			return cid
		except Exception as e:
			output = {"Error": str(e), "counts": {"error": str(e)}, "statevector": [str(e)], "memory": []}
			logging.defw_app(f"Error occurred: {output}")
			raise e

	def log_statistics(self, res):
		g_circ_metrics.add_time(res['creation_time'], res['launch_time'], "creation->launch")
		g_circ_metrics.add_time(res['resources_consumed_time'], res['exec_time'], "resources->exec")
		g_circ_metrics.add_time(res['exec_time'], res['completion_time'], "exec->completion")
		g_circ_metrics.add_time(res['cq_enqueue_time'], res['cq_dequeue_time'], "enqueue->dequeue")

	def result_reader(self, cid_list):
		circuit_run_timeout = 200
		total_circ = len(cid_list)
		total_circuits_completed = 0
		results = []
		qpm_results = []

		start = time.time()
		logging.defw_app(f"result reader start time: {start}")
		event_fd = self.event_api.fileno()
		while (time.time() - start < circuit_run_timeout and \
			total_circuits_completed != total_circ):
			readable, _, _ = select.select([event_fd], [], [], 1)
			if len(readable) > 0 and event_fd not in readable:
				raise DEFwError("Something wrong with select")
			if len(readable) > 0:
				r = self.event_api.get()
				results += r
				total_circuits_completed += len(r)

		logging.defw_app(f"Result reader thread ending. Events: {total_circuits_completed}. Expected: {total_circ}. Time: {time.time()}")
		for r in results:
			res = r.get_event()
			#logging.defw_app(f"{yaml.dump(res)}")
			exp = None
			for entry in cid_list:
				cid = res['cid']
				if cid == list(entry.keys())[0]:
					exp = entry[cid]['exp']
			if not exp:
				raise DEFwError(f"Couldn't find {res['cid']} in {cid_list}")
			qpm_results.append({'cid': res['cid'], 'res': res, 'exp': exp})

		return qpm_results

	# ASYNC
	def _run_async_job(self, job_id, circuits, options):
		if isinstance(circuits, qobj_module.QasmQobj):
			qobj = circuits
		elif isinstance(circuits, QuantumCircuit):
			circuits = [circuits]
			qobj = assemble(circuits)
		else:
			qobj = assemble(circuits)

		cid_list = []

		start = time.time()
		for (circuit,experiment) in zip(circuits, qobj.experiments):
			cid_list.append({self.run_experiment_async(circuit, experiment, options):
						{'exp': experiment, 'status': 0}})
		trigerring_time_taken = time.time() - start

		# instead of returning here, collect all the results, wait here!
		result_list = []

		#polling_start = time.time()

		# TODO: qfw_run_status currently calls read_cq for each cid, maybe we can have one call that returns all (read_all_cq: return all that is currently cmopleted) once completed in one go.
		# TODO: run to take a call back (1-batch_call_back gets called when entire batch is complete, 2- single_call_back is similar for single circuit). then don't poll.

		#TODO: When running multiple experiments, we're not associating a circuit with an
		# experiment, therefore, we endup using the same experiment to
		# report all the results, which causes the higher level stack to
		# get confused. What we need to do is we need to create a
		# dictionary of cide to experiment, so that we're able to report
		# results correctly.
		#logging.defw_app(f"Checking for completion of following cids: {cid_list}")

		res_wait_start = time.time()
		qpm_results = self.result_reader(cid_list)
		total_wait_time = time.time() - res_wait_start
#		while True:
#			for entry in cid_list:
#				cid = list(entry.keys())[0]
#				if entry[cid]['status'] == 1:
#					continue
#				logging.defw_app(f"Checking cid: {cid}")
#				try:
#					res = self.qpm.read_cq(cid=cid)
#					qpm_results.append({'cid': cid, 'res': res, 'exp': entry[cid]['exp']})
#					entry[cid]['status'] = 1
#					logging.defw_app(f"got result for experiment {entry[cid]['exp'].header.name}.\nAnd has value {res}")
#				except DEFwInProgress as e:
#					continue
#				except Exception as e:
#					raise e
#			if len(qpm_results) == len(cid_list):
#				logging.defw_app(f"Got all the results: {len(qpm_results)}, expected {len(cid_list)}")
#				break
#			time.sleep(0.0001)

		for qr in qpm_results:
			res = qr['res']
			self.log_statistics(res)
			output = res.get("result", {})
			#polling_time_taken = time.time() - polling_start
			#logging.defw_app(f"polling time taken={polling_time_taken}")

			# print("output = ", output)
			out = {"counts": output, "statevector": [], "memory": self.get_memory_from_counts(output), "time_taken": res['completion_time'] - res['exec_time']}

			experiment = qr['exp']
			result_dict = {
				"header": {
					"name": experiment.header.name,
					"memory_slots": experiment.config.memory_slots,
					"creg_sizes": experiment.header.creg_sizes,
				},
				"status": "DONE",
				"time_taken": out["time_taken"],
				"seed": options["seed"],
				"shots": options["shots"],
				"data": {"counts": out["counts"], "statevector": out["statevector"], "memory": out["memory"]},
				# "data": {"counts": output["counts"], "statevector": output["statevector"],},
				"success": True,
				"memory": True,
			}
			result_list.append(result_dict)

		result = {
			"backend_name": self._configuration_dict["backend_name"],
			"backend_version": self._configuration_dict["backend_version"],
			"qobj_id": qobj.qobj_id,
			"job_id": job_id,
			"results": result_list,
			"status": "COMPLETED",
			"success": True,
			"overall_time_taken": (trigerring_time_taken + total_wait_time),
			"time_taken": out["time_taken"],
			"memory": True,
		}

		# qc = circuits[0]
		# # Let's print meta here!!!
		# print("INDIVIDUAL CIRCUIT META ")
		# print("--")
		# meta_info = {
		# 	'num_ancillas': qc.num_ancillas,
		# 	'num_captured_vars': qc.num_captured_vars,
		# 	'num_clbits': qc.num_clbits,
		# 	'num_connected_components': qc.num_connected_components(),
		# 	'num_declared_vars': qc.num_declared_vars,
		# 	'num_input_vars': qc.num_input_vars,
		# 	'num_nonlocal_gates': qc.num_nonlocal_gates(),
		# 	'num_parameters': qc.num_parameters,
		# 	'num_qubits': qc.num_qubits,
		# 	'num_tensor_factors': qc.num_tensor_factors(),
		# 	'num_unitary_factors': qc.num_unitary_factors(),
		# 	'num_vars': qc.num_vars,
		# 	'width': qc.width(),
		# 	'depth': qc.depth(),
		# }
		# for key, value in meta_info.items():
		# 	print(f"{key}: {value}")
		# print("--")
		logging.defw_app(f"INDIVIDUAL CIRCUIT Time Taken by QFWBackend = {out['time_taken']}")
		logging.defw_app(f"overall_time_taken: {trigerring_time_taken}+{total_wait_time}")

		if time.time() - self.log_time > 30:
			g_circ_metrics.dump()
			g_rpc_metrics.dump()
			self.log_time = time.time()

		return Result.from_dict(result)

