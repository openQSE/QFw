import logging
import uuid
import time
import select

from qiskit import qasm2, QuantumCircuit
from qiskit.providers import JobV1 as Job
from qiskit.providers.jobstatus import JobStatus
from qiskit.quantum_info import Statevector
from qiskit.result import Result
from defw_exception import DEFwError, DEFwInProgress, DEFwNotFound
from .qfw_metadata import get_qubit_mapping


EXECUTION_CONTEXT_KEYS = (
	"reservation_id",
	"token",
	"timeout",
	"cancel_on_timeout",
)


def normalize_reservation_id(value):
	if value is None:
		return None
	if isinstance(value, str):
		value = value.strip()
		if not value:
			raise DEFwError("reservation_id must not be empty")
		try:
			value = int(value, 0)
		except ValueError as exc:
			raise DEFwError(
				f"reservation_id must be an unsigned 64-bit integer: "
				f"{value!r}") from exc
	if isinstance(value, bool) or not isinstance(value, int):
		raise DEFwError(
			f"reservation_id must be an unsigned 64-bit integer: "
			f"{value!r}")
	if value < 0 or value > 0xffffffffffffffff:
		raise DEFwError(
			f"reservation_id must fit in uint64_t: {value!r}")
	return value


class QFwJob(Job):
	def __init__(self, backend, qpm, event_api, qobj, options):
		self._job_id = str(uuid.uuid4())

		super().__init__(backend, self._job_id)

		self._cid_list = []

		self._qpm = qpm
		self._qpm_event_api = event_api

		self._backend = backend
		self._qobj = qobj
		self._options = options

		self._result_time = 0
		self._submission_time = 0

	def _run_experiment_async(self, circuit):
		self.start_time = time.time()
		# get qasm string from experiment
		qasm_string = qasm2.dumps(circuit)
		# get num_qubits from experiment
		num_qubits = circuit.num_qubits
		# let's form the info object to give to QFw!!
		info = {
			"qasm": qasm_string,
			"num_qubits": num_qubits,
			"num_shots": self.options()["shots"],
			"compiler": "staq",
		}
		qubit_mapping = get_qubit_mapping(circuit)
		if qubit_mapping:
			info["qubit_mapping"] = qubit_mapping
		if self._backend.returns_statevector():
			info["return_statevector"] = True

		try:
			context = self._execution_context()
			response = self._qpm.async_run(info, **context)
			cid = _async_response_cid(response)
			return cid
		except Exception as e:
			output = {"Error": str(e), "counts": {"error": str(e)}, "statevector": [str(e)], "memory": []}
			logging.defw_app(f"Error occurred: {output}")
			raise e

	def _execution_context(self):
		context = {
			key: self._options[key]
			for key in EXECUTION_CONTEXT_KEYS
			if key in self._options
		}
		if "reservation_id" in context:
			context["reservation_id"] = normalize_reservation_id(
				context["reservation_id"])
		if not context.get("reservation_id"):
			raise DEFwError("reservation_id is required for QPM execution")
		return context

	def submit(self):
		if isinstance(self._qobj, QuantumCircuit):
			circuits = [self._qobj]
		else:
			circuits = self._qobj

		start = time.time()
		for circuit in circuits:
			cid = self._run_experiment_async(circuit)
			self._cid_list.append({cid: {'exp': circuit, 'status': 0}})
		self._submission_time = time.time() - start
		return

	def _result_reader(self, cid_list):
		circuit_run_timeout = self._backend.COMPLETION_TIMEOUT_SEC
		total_circ = len(self._cid_list)
		total_circuits_completed = 0
		results = []
		qpm_results = []

		start = time.time()
		logging.defw_app(f"result reader start time: {start}")
		event_fd = self._qpm_event_api.fileno()
		expected_cids = {list(entry.keys())[0] for entry in cid_list}
		completed_cids = set()
		while time.time() - start < circuit_run_timeout and total_circuits_completed != total_circ:
			readable, _, _ = select.select([event_fd], [], [], 1)
			if len(readable) > 0 and event_fd not in readable:
				raise DEFwError("Something wrong with select")
			if len(readable) > 0:
				for event in self._qpm_event_api.get():
					payload = event.get_event()
					cid = payload.get("cid") if isinstance(payload, dict) else None
					if cid not in expected_cids or cid in completed_cids:
						continue
					results.append(event)
					completed_cids.add(cid)
					total_circuits_completed += 1

		logging.defw_app(
			f"Result reader thread ending. Events: {total_circuits_completed}."
			f" Expected: {total_circ}. Time: {time.time()}")
		for r in results:
			res = r.get_event()
			exp = None
			for entry in self._cid_list:
				cid = res['cid']
				if cid == list(entry.keys())[0]:
					exp = entry[cid]['exp']
			if not exp:
				raise DEFwError(f"Couldn't find {res['cid']} in {self._cid_list}")
			qpm_results.append({'cid': res['cid'], 'res': res, 'exp': exp})

		return qpm_results

	def _get_memory_from_counts(self, counts):
		m = []
		if not isinstance(counts, dict):
			return m
		for k, v in counts.items():
			sample = self._normalize_memory_sample(k)
			for i in range(v):
				m.append(sample)
		return m

	def _normalize_memory_sample(self, sample):
		if isinstance(sample, int):
			return hex(sample)

		sample = str(sample).strip()
		if sample.startswith(("0x", "0X")):
			return sample

		compact = sample.replace(" ", "")
		if compact and all(bit in "01" for bit in compact):
			return hex(int(compact, 2))

		return sample

	def _split_result_payload(self, output):
		if isinstance(output, dict) and (
			"counts" in output or "statevector" in output):
			counts = output.get("counts", {})
			statevector = self._build_statevector(
				output.get("statevector", None))
			metadata = self._result_metadata(output)
			return counts, statevector, metadata

		return output, [], {}

	def _result_metadata(self, output):
		metadata = {}
		qhw_result = output.get("qhw_result")
		if isinstance(qhw_result, dict):
			metadata["qhw_result"] = qhw_result
		return metadata

	def _build_statevector(self, payload):
		if not payload:
			return []

		if not isinstance(payload, dict):
			return Statevector(payload)

		if payload.get("type") != "statevector":
			raise DEFwError(f"Unsupported statevector payload: {payload}")

		data = payload.get("data", [])
		amplitudes = [complex(real, imag) for real, imag in data]
		return Statevector(amplitudes)

	def result(self):
		result_list = []

		res_wait_start = time.time()
		qpm_results = self._result_reader(self._cid_list)
		self._result_wait_time = time.time() - res_wait_start
		if not qpm_results:
			raise DEFwError("no QPM circuit results were received")

		if not qpm_results:
			# _result_reader returns early when it times out; with no completed
			# circuits there is nothing to assemble and the per-circuit `out`
			# referenced below (and in the job-level result) never binds. Fail
			# loudly with the cause instead of raising a bare NameError.
			raise DEFwError(
				f"QFw job {self._job_id} returned no circuit results within "
				f"{self._backend.COMPLETION_TIMEOUT_SEC}s "
				f"(expected {len(self._cid_list)} circuit(s))")

		for qr in qpm_results:
			res = qr['res']
			self._backend.log_statistics(res)
			output = res.get("result", {})
			counts, statevector, metadata = self._split_result_payload(output)

			out = {
				"counts": counts,
				"statevector": statevector,
				"metadata": metadata,
				"memory": self._get_memory_from_counts(counts),
				"time_taken": res['completion_time'] - res['exec_time']
			}

			circuit = qr['exp']
			# Extract metadata directly from circuit
			creg_sizes = [
				[creg.name, creg.size] for creg in circuit.cregs]
			result_dict = {
				"header": {
					"name": circuit.name if circuit.name else "circuit",
					"memory_slots": circuit.num_clbits,
					"creg_sizes": creg_sizes,
				},
				"status": "DONE",
				"time_taken": out["time_taken"],
				"seed": self.options()["seed_simulator"],
				"shots": self.options()["shots"],
				"data": {
					"counts": out["counts"],
					"statevector": out["statevector"],
					"metadata": out["metadata"],
					"memory": out["memory"]
				},
				"success": True,
				"memory": True,
			}
			result_list.append(result_dict)

		result = {
			"backend_name": self._backend.my_name(),
			"backend_version": self._backend.my_version(),
			"qobj_id": str(uuid.uuid4()),
			"job_id": self._job_id,
			"results": result_list,
			"status": "COMPLETED",
			"success": True,
			"overall_time_taken": (
				self._submission_time + self._result_wait_time),
			"time_taken": out["time_taken"],
			"memory": True,
		}

		logging.defw_app(
			f"INDIVIDUAL CIRCUIT Time Taken by QFwBackend = "
			f"{out['time_taken']}")
		logging.defw_app(
			f"overall_time_taken: {self._submission_time}+"
			f"{self._result_wait_time}")

		self._backend.dump_statistics()
		return Result.from_dict(result)

	def status(self):
		# TODO: Add a new API in the qpm which takes a list of CIDs and
		# does a onetime check to see if any of them is active. The API
		# returns true or false. true if any of the CIDs are still active.
		# false if all of them are complete.
		# This is better than having to call peek_cq() on every cid, which
		# will be too much communication with the backend.
		return NotImplementedError
		# query job_id and return one of -
		# JobStatus.INITIALIZING
		# JobStatus.QUEUED
		# JobStatus.VALIDATING
		# JobStatus.RUNNING
		# JobStatus.CANCELLED
		# JobStatus.DONE
		# JobStatus.ERROR
		# checking self._qfw_job_id
		try:
			self._qpm.read_cq(self._qfw_job_id)
		except Exception as e:
			if isinstance(e, DEFwInProgress):
				return JobStatus.RUNNING
			elif isinstance(e, DEFwError):
				return JobStatus.ERROR
			elif isinstance(e, DEFwNotFound):
				return JobStatus.ERROR

	def backend(self):
		return self._backend

	def qobj(self):
		return self._qobj

	def options(self):
		return self._options


def _async_response_cid(response):
	if isinstance(response, dict):
		return response["cid"]
	return response
