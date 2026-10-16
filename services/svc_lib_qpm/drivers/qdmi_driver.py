# Run jobs and normalize device queries through MQT Core's QDMI API.
#
# The packaged provider adapters own registration, catalogue defaults and
# session parameters through MQT Core's QDMI driver. QFw retains its resource
# policy, qhw records and interruptible job wait.
# Backend metadata that carries raw IQM architecture data stays with QRMI.

from .base_driver import BaseDriver, JobRecords, reservation_cache_key
from .qdmi_profiles import profile_for
from . import fomac_normalize
from defw_exception import DEFwExecutionError
from util import instrumentation
import json
import logging
import sys
import threading
import time


def _job_status(status):
	# QDMI Job.check() returns a Status enum; reduce it to a lowercase name.
	for attr in ("name", "value"):
		value = getattr(status, attr, None)
		if value is not None:
			return str(value).lower()
	return str(status).lower()


class QdmiDriver(BaseDriver):
	name = "qdmi"
	CAPABILITIES = frozenset({
		"get_device_info",
		"get_coupling_graph",
		"get_calibration_snapshot",
		"run_circuit",
		"get_task_timing",
		"get_task_metadata",
	})

	def __init__(self, descriptor=None):
		# Per-resource descriptor (descriptor.py); carries device identity for
		# binding/creds and, later, dynamic capability discovery.
		self._descriptor = descriptor or {}
		# The vendor-defined parts of QDMI for this resource's provider. An
		# unknown provider fails here, when the service starts, rather than
		# at the first call.
		self._profile = profile_for(self._descriptor)
		# Open device sessions, one per reservation (_credential_cache_key).
		# A session carries the token it opened with, so a circuit must never
		# run on one opened for another user, and it is dropped when its
		# reservation ends (evict_reservation).
		self._backends = {}
		self._backends_lock = threading.Lock()
		self._jobs = JobRecords("QDMI")

	# --- QDMI session / device binding -------------------------------

	def _access(self, credential=None):
		# The settings this resource's session opens with. What they are and
		# where they come from is the provider's business (qdmi_profiles):
		# IQM takes a server URL, an API token and a quantum computer alias,
		# Amazon Braket a device ARN and Region, IBM a backend and instance CRN.
		return self._profile.access(credential)

	@staticmethod
	def _credential_cache_key(credential=None):
		# Keyed as QrmiDriver._credential_cache_key keys its resources: a
		# reservation's session is its own, a credential from outside a
		# reservation is keyed by identity, and a call with no credential
		# shares the service's own session.
		credential = dict(credential or {})
		if not credential:
			return ("default",)
		reservation_id = credential.get("reservation_id")
		if reservation_id is not None:
			return reservation_cache_key(reservation_id)
		return (
			credential.get("url"),
			credential.get("provider_device_id"),
			credential.get("device_id"),
			credential.get("user"),
			credential.get("api_key") or credential.get("token"),
			credential.get("service_crn"),
		)

	def evict_reservation(self, reservation_id):
		# The reservation has ended: drop its session, and the token it
		# opened with goes too. A QDMI Device has no close(), and its
		# session ends when the last reference goes.
		with self._backends_lock:
			backend = self._backends.pop(
				reservation_cache_key(reservation_id), None)
		if backend is None:
			return False
		close = getattr(backend.device, "close", None)
		if callable(close):
			close()
		logging.debug(
			"shim: QDMI session for reservation %s dropped", reservation_id)
		return True

	def _backend(self, credential=None):
		# The provider adapter owns registration and session parameters.
		# Keep one adapter per reservation, never one shared user session.
		cache_key = self._credential_cache_key(credential)
		backend = self._backends.get(cache_key)
		if backend is not None:
			return backend
		with self._backends_lock:
			backend = self._backends.get(cache_key)
			if backend is not None:
				return backend
			access = self._access(credential)
			try:
				backend = self._profile.open_backend(access)
			except ImportError as exc:
				raise DEFwExecutionError(
					f"QDMI provider {self._profile.provider!r} needs "
					f"{self._profile.requires}; install the package set in "
					f"setup/qdmi-requirements.txt: {exc}") from exc
			except Exception as exc:
				raise DEFwExecutionError(
					"failed to open the QDMI backend for provider "
					f"{self._profile.provider!r} ({self._profile.requires}). "
					f"{exc}") from exc
			self._backends[cache_key] = backend
		logging.debug("shim: QDMI backend opened (%s)",
				self._profile.describe(access))
		return backend

	def _device(self, credential=None):
		return self._backend(credential).device

	def _ids(self):
		return (self._descriptor.get("provider", "iqm"),
				self._descriptor.get("id", "iqm-device"))

	# --- introspection facet: QDMI Device -> qhw (section 13 milestone) ---

	def get_device_info(self):
		provider, device_id = self._ids()
		topo = fomac_normalize.extract_topology(self._device())
		return fomac_normalize.to_device_record(
			topo, provider, device_id,
			technology=self._profile.technology())

	def get_coupling_graph(self, calibration_set_id=None):
		provider, device_id = self._ids()
		topo = fomac_normalize.extract_topology(self._device())
		return fomac_normalize.to_coupling_record(topo, provider, device_id)

	def get_calibration_snapshot(self, calibration_set_id=None):
		# QDMI exposes the device's live per-qubit coherence (T1/T2) and
		# per-gate fidelity; normalize them to qhw-calibration-v1. The record
		# names the calibration set it was read under when the device publishes
		# one. Selecting a *different* set is still a follow-up: the device
		# session always reflects the active one, so the argument is a no-op.
		provider, device_id = self._ids()
		cal = fomac_normalize.extract_calibration(
			self._device(),
			calibration_set_slot=self._profile.calibration_set_slot)
		return fomac_normalize.to_calibration_record(cal, provider, device_id)

	# --- execution: circuit -> provider program -> QDMI submit_job ------

	def run_circuit(self, circuit):
		# The circuit arrives in a format this QPM declares, QPY or OpenQASM 2
		# (see util.circuit_payload). The profile encodes it as the program
		# its device library takes (IQM_JSON for IQM, OpenQASM 3 for
		# Amazon Braket and IBM), then it is submitted through MQT Core's
		# QDMI job interface, polled to completion, and the counts are normalized to
		# qhw-result-v1 (the same record the QRMI path produces).
		# The reservation's credential, bound by the QPM controller
		# (UTIL_QPM attaches it before the circuit reaches the provider).
		credential = getattr(circuit, "provider_credential", None)
		with instrumentation.backend_phase("acquire"):
			backend = self._backend(credential=credential)
			device = backend.device
		info = getattr(circuit, "info", None) or {}
		cid = circuit.get_cid() if hasattr(circuit, "get_cid") else info.get("cid")
		from util.circuit_payload import qiskit_input
		source = qiskit_input(info)
		shots = int(info.get("num_shots", info.get("shots", 1024)))
		self._profile.check_shots(shots)
		timeout = self._profile.timeout_seconds(info)
		poll = float(info.get("poll_interval", 1.0))
		provider, device_id = self._ids()

		with instrumentation.qpm_transpile():
			program, program_format, measurement = self._profile.encode(
				self, source, info, backend)

		# Set by the shim QRC when the QPM cancels this circuit. A cancel that
		# arrives before submission starts nothing at the provider.
		cancel_event = getattr(circuit, "cancel_event", None)
		if cancel_event is not None and cancel_event.is_set():
			raise DEFwExecutionError(
				"QDMI job was cancelled before it was submitted")

		# The profile names the format rather than importing it, so nothing
		# here needs mqt.core until the job is about to be submitted.
		try:
			from mqt.core.qdmi import ProgramFormat
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to import mqt.core.qdmi ProgramFormat: {exc}") from exc
		try:
			fmt = getattr(ProgramFormat, program_format)
		except AttributeError as exc:
			raise DEFwExecutionError(
				"mqt.core.qdmi knows no program format "
				f"{program_format!r}") from exc

		timing = {}
		start = time.monotonic()
		# Native submission preserves the resource's Braket S3 destination,
		# which the provider's Qiskit backend does not expose as a run option.
		with instrumentation.backend_phase("submit"):
			try:
				job = device.submit_job(
					program, fmt, int(shots), **self._profile.job_kwargs(info))
			except Exception as exc:
				raise DEFwExecutionError(
					f"QDMI submit_job failed: {exc}") from exc
		timing["submit_seconds"] = time.monotonic() - start
		queue_position = self._queue_position(job)
		instrumentation.set_attribute(
			instrumentation.ATTR_VENDOR_QUEUE_POSITION, queue_position)

		collect = instrumentation.backend_phase("collect")
		collect.__enter__()
		instrumentation.set_attribute(
			instrumentation.ATTR_POLL_INTERVAL, float(poll))
		try:
			status = self._poll_job(
				job, timeout, poll, cancel_event=cancel_event)
		except BaseException:
			collect.__exit__(*sys.exc_info())
			raise
		timing["wait_seconds"] = (
			time.monotonic() - start - timing["submit_seconds"])
		try:
			# Job.id is an optional property, rather than a method.
			job_id = job.id
		except Exception as exc:
			# A device need not implement QDMI_JOB_PROPERTY_ID; carry on without
			# it, but leave a trace rather than dropping it silently.
			logging.debug("shim: QDMI job id unavailable: %s", exc)
			job_id = None
		instrumentation.set_attribute(
			instrumentation.ATTR_VENDOR_JOB_ID,
			None if job_id is None else str(job_id))
		if status != "completed":
			collect.__exit__(None, None, None)
			self._jobs.put({
				"id": job_id, "status": status, "cid": cid,
				"timing": timing, "shots": shots,
				"queue_position": queue_position})
			raise DEFwExecutionError(
				f"QDMI job {job_id} finished with status {status!r}")

		result_started = time.monotonic()
		try:
			counts = job.get_counts()
		except Exception as exc:
			collect.__exit__(*sys.exc_info())
			raise DEFwExecutionError(f"QDMI get_counts failed: {exc}") from exc
		collect.__exit__(None, None, None)
		timing["result_fetch_seconds"] = time.monotonic() - result_started
		timing["total_wall_seconds"] = time.monotonic() - start
		# QDMI keys a histogram by measured qubit, in the device library's
		# order. The profile maps it back onto the circuit's classical bits
		# where that differs (identity for IQM).
		counts = self._profile.result_counts(counts, measurement)

		record = fomac_normalize.to_result_record(
			counts, shots, provider, device_id, job_id=job_id,
			status="completed", queue_position=queue_position)
		self._jobs.put({
			"id": job_id, "status": "completed", "cid": cid,
			"timing": timing, "shots": shots,
			"queue_position": queue_position})
		return record

	def _serialize_program(self, iqm_circuit):
		# Serialize the transcoded IQM circuit to the single-circuit JSON QDMI's
		# IQM_JSON program expects. Prefer iqm-client's canonical serializer;
		# fall back to a generic coercion. Validated against the live IQM circuit
		# schema on hardware. The IQM profile calls this; it stays a driver
		# method so a test can stand in for it.
		from util.iqm_transcode import to_jsonable
		try:
			# to_json_dict() is typed to take a dict, but build_iqm_circuit
			# hands us an iqm.pulse Circuit *dataclass* (no model_dump), so it
			# must be coerced first -- passing the object straight in raises
			# "Object contains values that are not JSON serializable". The QRMI
			# leg never hit this because pydantic's RunRequest coerced the same
			# object for it. to_jsonable handles dataclasses/pydantic/UUIDs;
			# iqm-client's canonical encoder then applies on top when present.
			payload = to_jsonable(iqm_circuit)
			try:
				from iqm.iqm_client.util import to_json_dict
				payload = to_json_dict(payload)
			except ImportError:
				pass
			return json.dumps(payload)
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to serialize the IQM circuit for QDMI: {exc}") from exc

	def _queue_position(self, job):
		# The job's position in the provider queue at submission. QDMI 1.3.3
		# named QDMI_JOB_PROPERTY_QUEUEPOSITION, MQT Core 3.9 binds it, and
		# QDMI-on-IQM serves it from 1.4.0 on. Before that the IQM device
		# library parsed the value out of the submission response and only
		# wrote it to a log line, so it existed and was unreachable.
		#
		# Read once, straight after submit. It is a snapshot taken when the job
		# was accepted rather than a live depth, so re-reading it later would
		# describe a different moment without saying so.
		#
		# Like Job.id this is a PROPERTY, not a method -- calling it raises
		# TypeError, which is exactly the bug that silently emptied the job id
		# for months. None is a legitimate answer here: the device may not
		# report a position, and an older library will not have the property
		# at all.
		try:
			position = job.queue_position
		except Exception as exc:
			logging.debug("shim: QDMI queue position unavailable: %s", exc)
			return None
		return int(position) if position is not None else None

	def _poll_job(self, job, timeout, poll, cancel_event=None):
		# Poll QDMI job.check() until a terminal state, and return
		# completed, failed or cancelled.
		#
		# A cancel from the QPM (cancel_event, set by the shim QRC) or the
		# timeout cancels the provider job, so it does not keep running after
		# QFw has given up on it. The wait between polls ends as soon as a
		# cancel arrives.
		deadline = time.monotonic() + max(timeout, 0.0)
		polls = 0
		while True:
			if cancel_event is not None and cancel_event.is_set():
				self._cancel_job(job)
				return "cancelled"
			try:
				state = _job_status(job.check())
			except Exception as exc:
				raise DEFwExecutionError(
					f"QDMI job.check() failed: {exc}") from exc
			polls += 1
			instrumentation.add_event("poll", {"qfw.vendor.status": state})
			instrumentation.set_attribute(instrumentation.ATTR_POLL_COUNT, polls)
			if state == "done":
				return "completed"
			if state == "failed":
				return "failed"
			if state in ("canceled", "cancelled"):
				return "cancelled"
			if time.monotonic() >= deadline:
				cancel_error = self._cancel_job(job)
				cancelled = (
					f"job.cancel() failed: {cancel_error}" if cancel_error
					else "it was cancelled")
				raise DEFwExecutionError(
					f"QDMI job timed out after {timeout}s (status {state!r}), "
					f"and {cancelled}")
			if cancel_event is not None:
				cancel_event.wait(max(poll, 0.0))
			else:
				time.sleep(max(poll, 0.0))

	def _cancel_job(self, job):
		# Best effort, since the job may already have ended at the provider.
		# Returns the error text, or None when the cancel went through.
		try:
			job.cancel()
		except Exception as exc:
			logging.warning("shim: QDMI job.cancel() failed: %s", exc)
			return str(exc)
		return None

	# --- task timing / metadata (from run_circuit's job records) ---------

	def get_task_timing(self, cid=None):
		job = self._jobs.get(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"queue_position": job.get("queue_position"),
			"timing": job.get("timing") or {}}

	def get_task_metadata(self, cid=None):
		job = self._jobs.get(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"shots": job.get("shots"),
			"queue_position": job.get("queue_position"),
			"backend": self._descriptor.get("provider", "iqm")}
