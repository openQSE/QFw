import uuid
import time
import copy

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
from defw_exception import DEFwError, DEFwNotReady, DEFwInProgress, DEFwNotFound
from .qfw_lookup_service import get_qpm

# parts of this are inspired from https://github.com/pnnl/NWQ-Sim/blob/main/qiskit/qiskit_nwqsim_provider/nwqsim_simulator.py; Thank you Dr. Ang Li.
# and from https://github.com/Qiskit/qiskit-aer/blob/main/qiskit_aer/backends/aerbackend.py; Thank you Qiskit-Aer

qpm = None

class QFWBackend(BackendV2):
	def __init__(self, simulator = "nwqsim", target = None, properties = None):
		global qpm

		qpm = get_qpm()

		super().__init__(name="QFW Simulator")
		# self._target = Target(description="QFW Simulator Target", num_qubits=40)
		self._target = target
		self._properties = properties
		
		# Custom option values for config, properties
		self._options_configuration = {}
		self._options_properties = {}

		self.options.set_validator("shots", (1, 65536))
		self.simulator = simulator
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
		global qpm

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
			"simulator": self.simulator,
			"num_shots": options["shots"],
			"compiler": "staq", # only for tnqvm, it is not used by nwqsim, TODO: need to think of a cleaner way..
		}
		try:
			cid = qpm.create_circuit(info)
			rc, output = qpm.sync_run(cid)
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
		global qpm

		self.start_time = time.time()
		# get qasm string from experiment
		qasm_string = qasm2.dumps(circuit)
		# get num_qubits from experiment
		num_qubits = circuit.num_qubits
		# let's form the info object to give to QFw!!
		info = {
			"qasm": qasm_string,
			"num_qubits": num_qubits,
			"simulator": self.simulator,
			"num_shots": options["shots"],
			"compiler": "staq", # only for tnqvm, it is not used by nwqsim, TODO: need to think of a cleaner way..
		}
		try:
			cid = qpm.create_circuit(info)
			qpm.async_run(cid)
			return cid
		except Exception as e:
			print("Error! = ", str(e))
			output = {"Error": str(e), "counts": {"error": str(e)}, "statevector": [str(e)], "memory": []}
			# output = {"Error": str(e), "counts": {"error": str(e)}, "statevector": [str(e)]}
			return None

	# ASYNC
	def _run_async_job(self, job_id, circuits, options):
		global qpm

		if isinstance(circuits, qobj_module.QasmQobj):
			qobj = circuits
		elif isinstance(circuits, QuantumCircuit):
			circuits = [circuits]
			qobj = assemble(circuits)
		else:
			qobj = assemble(circuits)

		ran_cid_list = []

		start = time.time()
		for (circuit,experiment) in zip(circuits, qobj.experiments):
			ran_cid_list.append(self.run_experiment_async(circuit, experiment, options))
		trigerring_time_taken = time.time() - start

		# instead of returning here, collect all the results, wait here!
		result_list = []

		polling_start = time.time()

		# TODO: qfw_run_status currently calls read_cq for each cid, maybe we can have one call that returns all (read_all_cq: return all that is currently cmopleted) once completed in one go.
		# TODO: run to take a call back (1-batch_call_back gets called when entire batch is complete, 2- single_call_back is similar for single circuit). then don't poll.

		for each_cid in ran_cid_list:
			res = qpm.read_cq(qrc_type=self.simulator)

			while True:
				try:
					time.sleep(5)
					res = qpm.read_cq(qrc_type=self.simulator)
					break
				except DEFwInProgress as e:
					continue
				except Exception as e:
					raise e

			print(f"got result of type {type(res)}")

			# print("res = ", res)
			output = res.get("result", {})
			polling_time_taken = time.time() - polling_start

			# print("output = ", output)
			out = {"counts": output, "statevector": [], "memory": self.get_memory_from_counts(output), "time_taken": res["time_taken"]}

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
			"overall_time_taken": (trigerring_time_taken + polling_time_taken),
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
		print("INDIVIDUAL CIRCUIT Time Taken by QFWBackend = ", out["time_taken"])

		return Result.from_dict(result)

