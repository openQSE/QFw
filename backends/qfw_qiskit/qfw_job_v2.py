"""Qiskit job lifecycle backed by libquantum operations."""

from dataclasses import dataclass
import time
import uuid

from qiskit import QuantumCircuit
from qiskit.providers import JobV1
from qiskit.providers.exceptions import JobError, JobTimeoutError
from qiskit.providers.jobstatus import JobStatus
from qiskit.result import Result


@dataclass
class _ExperimentOperation:
	index: int
	circuit: QuantumCircuit
	op_id: str | None = None
	completion: object | None = None
	submission_error: Exception | None = None
	last_status: object | None = None


class QFwJobV2(JobV1):
	"""One Qiskit job grouping an ordered libquantum operation batch."""

	def __init__(self, backend, circuits, run_options):
		super().__init__(backend, str(uuid.uuid4()))
		self._backend = backend
		self._context = backend._context
		self._route = backend._route
		self._circuits = _normalize_circuits(circuits)
		self._execution_options = _execution_options(backend, run_options)
		self._experiments = [
			_ExperimentOperation(index, circuit)
			for index, circuit in enumerate(self._circuits)
		]
		self._queue = None
		self._submitted = False
		self._cached_result = None

	def submit(self):
		if self._submitted:
			raise JobError("QFwJobV2 has already been submitted")
		self._submitted = True
		compiled = []
		for circuit in self._circuits:
			program = self._context.import_program(circuit)
			compiled.append(self._context.compile(self._route, program))
		self._queue = self._context.create_completion_queue(self._route)
		for index, (experiment, executable) in enumerate(zip(
				self._experiments, compiled)):
			try:
				experiment.op_id = self._context.run(
					self._route,
					executable,
					self._queue,
					self._execution_options,
				)
			except Exception as exc:
				experiment.submission_error = exc
				for pending in self._experiments[index + 1:]:
					pending.submission_error = JobError(
						"not submitted after an earlier submission failure")
				break

	def status(self):
		if not self._submitted:
			return JobStatus.INITIALIZING
		if any(item.submission_error for item in self._experiments):
			return JobStatus.ERROR
		accepted = [item for item in self._experiments if item.op_id is not None]
		if not accepted:
			return JobStatus.ERROR
		states = []
		for experiment in accepted:
			if experiment.completion is not None:
				states.append(_completion_state(experiment.completion))
				continue
			status = self._context.status(experiment.op_id)
			experiment.last_status = status
			states.append(_status_state(status))
		if all(state == "done" for state in states):
			return JobStatus.DONE
		if all(state == "cancelled" for state in states):
			return JobStatus.CANCELLED
		if any(state in {"failed", "cancelled"} for state in states):
			return JobStatus.ERROR
		if any(state == "running" for state in states):
			return JobStatus.RUNNING
		return JobStatus.QUEUED

	def cancel(self):
		if not self._submitted:
			return False
		accepted = [
			item for item in self._experiments
			if item.op_id is not None and item.completion is None
		]
		if not accepted:
			return self.status() is JobStatus.CANCELLED
		success = True
		for experiment in accepted:
			try:
				response = self._context.cancel(experiment.op_id)
				experiment.last_status = response
			except Exception:
				success = False
		return success

	def result(self, timeout=None):
		if self._cached_result is not None:
			return self._cached_result
		if not self._submitted:
			raise JobError("QFwJobV2 has not been submitted")
		deadline = None if timeout is None else time.monotonic() + timeout
		unfinished = {
			item.op_id for item in self._experiments if item.op_id is not None
		}
		while unfinished:
			remaining = None if deadline is None else deadline - time.monotonic()
			if remaining is not None and remaining <= 0:
				raise JobTimeoutError("QFwJobV2 result timed out")
			if not self._queue.wait(remaining):
				raise JobTimeoutError("QFwJobV2 result timed out")
			for completion in self._queue.get():
				experiment = next((
					item for item in self._experiments
					if item.op_id == completion.op_id
				), None)
				if experiment is None or experiment.completion is not None:
					continue
				experiment.completion = completion
				unfinished.discard(completion.op_id)
		if any(
			item.op_id is None and item.submission_error is None
			for item in self._experiments
		):
			raise JobError("QFwJobV2 batch was only partially submitted")
		self._cached_result = _qiskit_result(self)
		self._queue.close()
		return self._cached_result

	def diagnostic_operations(self):
		"""Return safe operator diagnostics without exposing route objects."""
		return tuple({
			"index": item.index,
			"submitted": item.op_id is not None,
			"status": item.last_status,
			"submission_error": (
				str(item.submission_error) if item.submission_error else None
			),
		} for item in self._experiments)


def _normalize_circuits(circuits):
	if isinstance(circuits, QuantumCircuit):
		return (circuits,)
	if isinstance(circuits, (str, bytes)):
		raise TypeError("circuits must contain QuantumCircuit objects")
	try:
		values = tuple(circuits)
	except TypeError as exc:
		raise TypeError(
			"circuits must be a QuantumCircuit or circuit sequence") from exc
	if not values or not all(isinstance(item, QuantumCircuit) for item in values):
		raise TypeError("circuits must contain QuantumCircuit objects")
	return values


def _execution_options(backend, run_options):
	unknown = set(run_options).difference({
		"shots", "timeout", "memory", "return_statevector",
		"seed_simulator",
	})
	if unknown:
		raise ValueError(
			f"unsupported QFwBackendV2 run options: {', '.join(sorted(unknown))}")
	shots = run_options.get("shots", backend.options.shots)
	timeout = run_options.get("timeout", backend.options.timeout)
	memory = run_options.get("memory", backend.options.memory)
	if not isinstance(memory, bool):
		raise ValueError("memory must be a boolean")
	return_statevector = run_options.get(
		"return_statevector", backend.options.return_statevector)
	if not isinstance(return_statevector, bool):
		raise ValueError("return_statevector must be a boolean")
	seed = run_options.get("seed_simulator", backend.options.seed_simulator)
	if seed is not None:
		raise ValueError("seed_simulator is not supported by this QPM route")
	import libquantum as lq
	return lq.ExecutionOptions(
		shots=shots,
		timeout=timeout,
		values={
			"memory": memory,
			"return_statevector": return_statevector,
		},
	)


def _status_state(status):
	if not isinstance(status, dict):
		return "queued"
	value = status.get("state", status.get("status", "queued"))
	value = str(value).lower().replace("_", "-")
	if value in {"completed", "complete", "done", "success", "succeeded"}:
		return "done"
	if value in {"cancelled", "canceled"}:
		return "cancelled"
	if value in {"failed", "error", "timed-out", "timeout"}:
		return "failed"
	if value in {"running", "executing", "active"}:
		return "running"
	return "queued"


def _completion_state(completion):
	if completion.error is None:
		return "done"
	code = completion.error.detail.code
	return "cancelled" if "cancel" in code else "failed"


def _qiskit_result(job):
	results = []
	overall_success = True
	for experiment in job._experiments:
		completion = experiment.completion
		success = completion is not None and completion.error is None
		overall_success = overall_success and success
		payload = completion.result if success else {}
		if payload is None:
			payload = {}
		if not isinstance(payload, dict):
			raise JobError("unsupported QRI result payload")
		payload = _normalized_payload(payload)
		counts, memory, shots, statevector = _validated_payload(
			job, experiment, payload)
		data = {"counts": counts, "memory": memory}
		if statevector is not None:
			data["statevector"] = statevector
		metadata = dict(payload.get("metadata") or {})
		if "qhw_result" in payload:
			metadata["qhw_result"] = payload["qhw_result"]
		if not success:
			metadata["error"] = (
				str(completion.error) if completion is not None
				else str(experiment.submission_error or "not submitted")
			)
		results.append({
			"success": success,
			"status": "DONE" if success else "ERROR",
			"shots": shots,
			"header": {
				"name": experiment.circuit.name or f"circuit-{experiment.index}",
				"memory_slots": experiment.circuit.num_clbits,
				"metadata": dict(experiment.circuit.metadata or {}),
			},
			"data": data,
			"metadata": metadata,
		})
	return Result.from_dict({
		"backend_name": job.backend().name,
		"backend_version": job.backend().backend_version,
		"qobj_id": None,
		"job_id": job.job_id(),
		"success": overall_success,
		"status": "COMPLETED" if overall_success else "ERROR",
		"results": results,
	})


def _memory_from_counts(counts):
	if not isinstance(counts, dict):
		raise JobError("QRI counts must be a mapping")
	memory = []
	for sample, count in counts.items():
		if isinstance(count, bool) or not isinstance(count, int) or count < 0:
			raise JobError("QRI count values must be nonnegative integers")
		memory.extend([sample] * count)
	return memory


def _validated_payload(job, experiment, payload):
	identity = payload.get("experiment_id", payload.get("experiment_name"))
	if identity is not None and identity != experiment.circuit.name:
		raise JobError("QRI result experiment identity does not match")
	counts = payload.get("counts", {})
	if not isinstance(counts, dict):
		raise JobError("QRI counts must be a mapping")
	for sample in counts:
		text = str(sample).replace(" ", "")
		if text.startswith(("0x", "0X")):
			int(text, 16)
		elif not text or any(bit not in "01" for bit in text):
			raise JobError("QRI count keys must be binary or hexadecimal")
		elif len(text) > experiment.circuit.num_clbits:
			raise JobError("QRI count key exceeds the circuit bit width")
	memory = payload.get("memory")
	if memory is None:
		memory = _memory_from_counts(counts)
	elif not isinstance(memory, (tuple, list)):
		raise JobError("QRI memory must be a sequence")
	else:
		memory = list(memory)
	shots = payload.get("shots", job._execution_options.shots)
	if shots is not None:
		if isinstance(shots, bool) or not isinstance(shots, int) or shots < 1:
			raise JobError("QRI shots must be a positive integer")
		if counts and sum(counts.values()) != shots:
			raise JobError("QRI counts do not match the shot count")
		if memory and len(memory) != shots:
			raise JobError("QRI memory does not match the shot count")
	statevector = payload.get("statevector")
	if statevector is not None:
		if isinstance(statevector, dict):
			from qiskit.quantum_info import Statevector
			from util.qpm.statevector import decode_statevector_payload

			statevector = Statevector(decode_statevector_payload(statevector))
		try:
			size = len(statevector)
		except TypeError as exc:
			raise JobError("QRI statevector must be a sequence") from exc
		if size != 2 ** experiment.circuit.num_qubits:
			raise JobError("QRI statevector has the wrong dimension")
	return counts, memory, shots, statevector


def _normalized_payload(payload):
	envelope_fields = {
		"counts", "experiment_id", "experiment_name", "memory", "metadata",
		"qhw_result", "shots", "statevector",
	}
	if payload and not envelope_fields.intersection(payload):
		return {"counts": payload}
	return payload
