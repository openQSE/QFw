# A Pulser RemoteConnection that runs sequences on a QFw QPM, so a Pulser
# program targets QFw the way it targets Pasqal Cloud:
#
#	connection = QFwConnection()
#	backend = pulser.QPUBackend(sequence, connection=connection)
#	results = backend.run([{"runs": 100}], wait=True)
#
# Each job is one circuit on the QPM, sent in Pulser's abstract representation
# (util.circuit_payload). A sequence with variables is built with each job's
# values first, since the libraries behind the QPM take one concrete sequence
# per job. A batch is the jobs of one submit and exists only in this
# connection, which tracks it by polling the QPM's completion queue.
#
# Once a job is accepted its batch stays reachable: a submit that fails part
# way, or a wait that times out or cannot poll, raises QFwBatchError naming
# the batch, which RemoteResults(batch_id, connection) follows and
# cancel_batch stops. Only a failed submit cancels on its own, since the
# caller never asked for a partial batch.
#
# fetch_available_devices returns the Pulser devices the QPM runs, fetched by
# its library: the QPU itself, or for an emulator the real devices it accepts
# sequences for. QPUBackend checks a sequence against their current specs, and
# a program can build its register on a calibrated layout.

import os
import time
import uuid

from pulser import QPUBackend
from pulser.backend.remote import (
	BatchStatus,
	JobStatus,
	RemoteConnection,
	RemoteResults,
	RemoteResultsError,
)
from pulser.result import SampledResult

from defw_exception import DEFwError
from qfw_qiskit.qfw_job import (
	SHIM_LIB_ENV,
	_async_response_cid,
	failure_message,
	normalize_reservation_id,
	normalize_shim_library,
)
from qfw_qiskit.qfw_lookup_service import get_qpm, get_qpm_telemetry
from qfw_qiskit.qpm_selection import qpm_selection_for_provider
from util.circuit_payload import PULSER, encode_pulser_sequence

# The QPM lifecycle state of a task that has reached the provider. Every
# earlier state is still pending.
RUNNING_STATE = "submitted"
UNFINISHED = (JobStatus.PENDING, JobStatus.RUNNING)
# Completion poll outcomes after which the job's completion never arrives.
LOST_OUTCOMES = (
	"INVALID_RESERVATION", "MISSING_RESERVATION", "NO_LONGER_RETAINED")
# cancel_task outcomes for a cancel that took, or a job that had finished.
CANCEL_TAKEN = ("CANCELLED", "CANCEL_PENDING", "COMPLETED")
FOLLOW = (
	"follow it with RemoteResults(batch_id, connection), or stop it with "
	"connection.cancel_batch(batch_id)")


class QFwBatchError(DEFwError):
	# A batch with accepted jobs that submit could not hand back as
	# RemoteResults. batch_id still names it on the connection.
	def __init__(self, message, batch_id):
		super().__init__(message)
		self.batch_id = batch_id


class _Job:
	def __init__(self, cid, sequence):
		self.cid = cid
		self.sequence = sequence
		self.status = JobStatus.PENDING
		self.result = None
		self.error = None


class QFwConnection(RemoteConnection):
	def __init__(self, service_id=None, reservation_id=None, token=None,
		     lib=None, poll_interval=1.0, timeout=None, lookup_timeout=None):
		# service_id picks the QPM among the reservations the launcher
		# exported (QFW_RESERVATIONS); without them the lookup finds the one
		# Pasqal QPM. reservation_id overrides the reservation's id,
		# and token is the reservation's token when the QPM wants one. lib
		# picks the shim library, as QFwBackend's lib option does, and
		# QFW_SHIM_LIB is the default. timeout bounds how long a submit with
		# wait=True waits, None for no bound. It ends the wait, not the jobs:
		# the provider's deadline is the device's job-timeout-seconds.
		selection = qpm_selection_for_provider("pasqal")
		lookup = {} if lookup_timeout is None else {"timeout": lookup_timeout}
		self._qpm, reserved = get_qpm(
			selection["qpm_type"], selection["qpm_capabilities"],
			provider=selection["provider"], service_id=service_id,
			return_reservation=True, **lookup)
		reservation_id = normalize_reservation_id(
			reservation_id if reservation_id is not None else reserved)
		if not reservation_id:
			raise DEFwError("reservation_id is required for QPM execution")
		self._context = {"reservation_id": reservation_id}
		if token:
			self._context["token"] = token
		properties = getattr(self._qpm, "qpm_properties", None) or {}
		formats = properties.get("circuit_formats") or ()
		if PULSER not in formats:
			raise DEFwError(
				f"the QPM reads {', '.join(formats) or 'openqasm2'}, not "
				"Pulser sequences. Reserve a Pasqal device")
		self._lib = normalize_shim_library(
			lib or os.environ.get(SHIM_LIB_ENV), properties)
		self._poll_interval = float(poll_interval)
		self._timeout = timeout
		self._batches = {}

	def submit(self, sequence, wait=False, open=False, batch_id=None,
		   **kwargs):
		if open or batch_id is not None:
			raise NotImplementedError(
				"QFwConnection runs each submission as one closed batch")
		# QPUBackend.run checks the sequence against the device's current
		# specs but hands this the sequence it was given, so the check is
		# made again here and what is sent is what was checked, as Pasqal
		# Cloud's own connection does.
		job_params = kwargs.get("job_params") or [{}]
		sequence = self.update_sequence_device(sequence)
		QPUBackend.validate_job_params(job_params, sequence.device.max_runs)
		variable = sequence.is_parametrized() or sequence.is_register_mappable()
		if not variable:
			# One sequence for every job, so measure and encode it once.
			built = self._add_measurement_to_sequence(sequence)
			encoded = encode_pulser_sequence(built)
		# Every job is built before any is sent, so a job's bad variables
		# fail the submit before the provider has run anything.
		built_jobs = []
		for params in job_params:
			if variable:
				built = self._add_measurement_to_sequence(
					sequence.build(**(params.get("variables") or {})))
				encoded = encode_pulser_sequence(built)
			info = {
				"num_qubits": len(built.register.qubit_ids),
				"num_shots": int(params["runs"]),
				**encoded,
			}
			if self._lib:
				info["lib"] = self._lib
			built_jobs.append((built, info))
		batch_id = uuid.uuid4().hex
		jobs = self._batches[batch_id] = {}
		try:
			for built, info in built_jobs:
				cid = _async_response_cid(
					self._qpm.async_run(info, **self._context))
				jobs[str(cid)] = _Job(cid, built)
		except BaseException as error:
			if not jobs:
				del self._batches[batch_id]
				raise
			# The caller asked for the whole batch, so the jobs already
			# accepted are cancelled, each on its own, and kept here so
			# what became of them can still be read. An interrupt cancels
			# them too and is raised as it is.
			failed = self._cancel(jobs.values(), "batch submission failed")
			if not isinstance(error, Exception):
				raise
			raise QFwBatchError(
				f"submitting QFw batch {batch_id} failed after "
				f"{len(jobs)} of {len(job_params)} jobs ({error}); "
				f"{self._cancel_report(jobs.values(), failed)}",
				batch_id) from error
		if wait:
			self._wait(batch_id)
		return RemoteResults(batch_id, self)

	def _wait(self, batch_id):
		deadline = (None if self._timeout is None
			else time.monotonic() + float(self._timeout))
		while True:
			try:
				status = self._get_batch_status(batch_id)
			except Exception as error:
				raise QFwBatchError(
					f"polling QFw batch {batch_id} failed ({error}); "
					f"{FOLLOW}", batch_id) from error
			if status not in (BatchStatus.PENDING, BatchStatus.RUNNING):
				return
			if deadline is not None and time.monotonic() >= deadline:
				raise QFwBatchError(
					f"QFw batch {batch_id} did not finish in "
					f"{self._timeout}s and is still running; {FOLLOW}",
					batch_id)
			time.sleep(self._poll_interval)

	def cancel_batch(self, batch_id):
		# Ask the QPM to cancel the batch's unfinished jobs. Their status
		# shows the outcome once read, as a job may finish first. A job
		# whose completion cannot be read is asked too, since a failed poll
		# is when _wait points here; the QPM answers COMPLETED for one that
		# has finished.
		jobs = self._jobs(batch_id)
		for job in jobs.values():
			try:
				self._update(job)
			except Exception:
				pass
		unfinished = [job for job in jobs.values() if job.status in UNFINISHED]
		failed = self._cancel(unfinished, "cancelled by the client")
		if failed:
			raise QFwBatchError(
				f"cancelling QFw batch {batch_id}: "
				f"{self._cancel_report(unfinished, failed)}", batch_id)

	def _cancel(self, jobs, reason):
		# One request per job, so one that fails does not leave the rest
		# running. The QPM answers a cancel it refuses (an unknown task, a
		# reservation that no longer holds) with its outcome rather than an
		# error. Returns the failures by cid.
		failed = {}
		for job in jobs:
			try:
				answer = self._qpm.cancel_task(
					cid=job.cid, reason=reason, **self._context)
			except Exception as error:
				failed[job.cid] = error
				continue
			outcome = answer.get("outcome") if isinstance(answer, dict) \
				else None
			if outcome is not None and outcome not in CANCEL_TAKEN:
				failed[job.cid] = (
					f"the QPM answered {outcome} ({answer.get('reason')})")
		return failed

	@staticmethod
	def _cancel_report(jobs, failed):
		requested = [str(job.cid) for job in jobs if job.cid not in failed]
		report = [f"cancellation requested for {', '.join(requested)}"
			if requested else "no cancellation went through"]
		report += [f"could not cancel {cid}: {error}"
			for cid, error in failed.items()]
		return "; ".join(report)

	def _update(self, job):
		# Read the job's completion once it is ready, which also removes it
		# from the QPM's queue, so the outcome is kept here from then on.
		if job.status not in UNFINISHED:
			return
		completion = self._qpm.read_cq(cid=job.cid, **self._context)
		if not completion.get("completion_ready"):
			outcome = completion.get("outcome")
			if outcome in LOST_OUTCOMES:
				job.status = JobStatus.ERROR
				job.error = (
					f"QFw circuit {job.cid}: {outcome} "
					f"({completion.get('reason')})")
			elif completion.get("lifecycle_state") == RUNNING_STATE:
				job.status = JobStatus.RUNNING
			return
		if completion.get("rc") == 0:
			counts = (completion.get("result") or {}).get("counts") or {}
			job.result = SampledResult(
				atom_order=tuple(job.sequence.register.qubit_ids),
				meas_basis=job.sequence.get_measurement_basis(),
				bitstring_counts=dict(counts))
			job.status = JobStatus.DONE
		elif (completion.get("outcome") == "CANCELLED"
				or completion.get("reason") == "provider-cancelled"):
			# The QPM's own cancel, or a provider job it cancelled.
			job.status = JobStatus.CANCELED
		else:
			job.status = JobStatus.ERROR
			job.error = failure_message(completion)

	def _jobs(self, batch_id):
		try:
			return self._batches[batch_id]
		except KeyError:
			raise RemoteResultsError(
				f"QFwConnection has no batch {batch_id!r}") from None

	def _query_job_progress(self, batch_id):
		jobs = self._jobs(batch_id)
		for job in jobs.values():
			self._update(job)
		return {job_id: (job.status, job.result)
			for job_id, job in jobs.items()}

	def _fetch_result(self, batch_id, job_ids):
		jobs = self._jobs(batch_id)
		results = []
		for job_id in job_ids or list(jobs):
			job = jobs[job_id]
			self._update(job)
			if job.result is None:
				raise RemoteResultsError(
					job.error or f"job {job_id} is {job.status.name}")
			results.append(job.result)
		return results

	def _get_batch_status(self, batch_id):
		statuses = {
			status for status, _result
			in self._query_job_progress(batch_id).values()}
		if statuses & set(UNFINISHED):
			return (BatchStatus.PENDING if statuses == {JobStatus.PENDING}
				else BatchStatus.RUNNING)
		if statuses == {JobStatus.DONE}:
			return BatchStatus.DONE
		if JobStatus.ERROR in statuses:
			return BatchStatus.ERROR
		return BatchStatus.CANCELED

	def fetch_available_devices(self):
		# The QPM's telemetry API serves get_backend_info, beside the
		# execution API this connection runs jobs through. A QPM that answers
		# with no Pulser device raises NotImplementedError, which Pulser
		# treats as "use the sequence's own device"; any other failure is
		# raised as it is.
		from pulser.abstract_repr import deserialize_device
		telemetry = get_qpm_telemetry(self._qpm)
		# Read as the reservation, whose credential the QPM resolves itself:
		# a QPU's specs need a login, an emulator's are public.
		info = telemetry.get_backend_info(lib=self._lib, **self._context)
		if not info.get("pulser_devices"):
			raise NotImplementedError(
				f"the QPM serves no Pulser device: {info!r}")
		return {name: deserialize_device(specs)
			for name, specs in info["pulser_devices"].items()}

	def _get_job_ids(self, batch_id):
		return list(self._jobs(batch_id))

	def supports_open_batch(self):
		return False
