# QDMI driver — device-introspection facet, via QDMI's FoMaC query interface.
#
# QDMI is vendor-neutral: MQT Core's FoMaC query interface (bound in Python as
# mqt.core.qdmi) exposes the device's sites (real qubit names + T1/T2), the
# operations (loci + fidelity), and the coupling map. This driver reads that
# directly and normalizes it to the provider-neutral qhw schema
# (fomac_normalize) -- no Qiskit Target, no raw vendor data. QDMI is
# session-based and strong on device & calibration introspection. QRMI remains
# the default execution owner, while this driver also implements execution for
# explicit QDMI comparisons.
#
# Milestone status (design doc qpu-frontend-contract.md section 13):
# get_device_info / get_coupling_graph normalize the device topology, and
# get_calibration_snapshot reads the device's live per-qubit coherence (T1/T2)
# and per-gate fidelity through FoMaC -- all with the device's real qubit labels
# (e.g. "QB1"). get_backend_info / get_dynamic_backend_info stay with QRMI
# because their native shape carries raw IQM architecture data that QDMI does
# not expose.
#
# QDMI is one interface, but the vendor-defined parts differ per device
# library: how a session is opened and from which settings, which program
# format a job takes, what the CUSTOM slots mean, and how counts are keyed.
# Those live in a per-provider profile (qdmi_profiles.py), chosen from the
# resource descriptor's provider. This driver keeps the session and job
# lifecycle (open, submit, poll, cancel, results) in one place and asks the
# profile for the rest.

from .base_driver import BaseDriver
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
	# FoMaC Job.check() returns a Status enum; reduce it to a lowercase name.
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
		# Open device sessions, one per credential (_credential_cache_key).
		# A session carries the token it opened with, so a circuit must never
		# run on one opened for another user.
		self._devices = {}
		self._devices_lock = threading.Lock()
		self._last_job = None

	# --- QDMI session / device binding -------------------------------

	def _access(self, credential=None):
		# The settings this resource's session opens with. What they are and
		# where they come from is the provider's business (qdmi_profiles):
		# IQM takes a server URL, an API token and a quantum computer alias,
		# Braket a device ARN and a Region.
		return self._profile.access(credential)

	@staticmethod
	def _credential_cache_key(credential=None):
		# The same identity QrmiDriver._credential_cache_key keys its
		# resources by. A call outside a reservation has no credential and
		# shares the service's own session.
		credential = dict(credential or {})
		if not credential:
			return ("default",)
		return (
			credential.get("url"),
			credential.get("provider_device_id"),
			credential.get("device_id"),
			credential.get("user"),
			credential.get("api_key") or credential.get("token"),
		)

	def _device(self, credential=None):
		# Lazy: open the QDMI device through MQT Core's QDMI driver once per
		# credential (see the note on credential below). Import
		# and construction are deferred so the service/Frontend build and route
		# even where the libraries are absent or credentials are unset; only a
		# real introspection call needs a live device. The profile names the
		# device library and the stable device ID to register it under, and
		# maps this resource's settings onto the session parameters, because
		# QDMI's BASEURL, TOKEN and CUSTOM slots carry different things for
		# different vendors.
		cache_key = self._credential_cache_key(credential)
		device = self._devices.get(cache_key)
		if device is not None:
			return device
		try:
			from mqt.core.qdmi.driver import (open_device,
					register_device_if_absent)
		except Exception as exc:
			raise DEFwExecutionError(
				"failed to import the QDMI driver API (mqt.core.qdmi.driver). "
				"Install mqt-core >= 3.9 before using the QDMI driver: "
				f"{exc}") from exc
		access = self._access(credential)
		try:
			definition = self._profile.definition(access)
		except DEFwExecutionError:
			raise
		except Exception as exc:
			raise DEFwExecutionError(
				"failed to import the QDMI device library for provider "
				f"{self._profile.provider!r}. Install {self._profile.requires} "
				f"before using the QDMI driver: {exc}") from exc
		# Registration only validates and stores the definition -- it loads no
		# native code, and register_device_if_absent makes a second driver
		# instance in the same process a no-op instead of a duplicate-ID error.
		# open_device then allocates a fresh QDMI device session, applies these
		# parameters, and initializes it (the device library fetches the
		# device/calibration data during init). A query before a session is
		# initialized returns a bad-session-state error, so surface an init
		# failure here as exactly that: the session could not be opened.
		#
		# credential is the reservation's bound provider credential (the
		# circuit's provider_credential), so the session opens as the user
		# who reserved the device, not as the account the service runs under.
		# The lock keeps two circuits for one credential from each opening a
		# session.
		with self._devices_lock:
			device = self._devices.get(cache_key)
			if device is not None:
				return device
			try:
				register_device_if_absent(definition)
				device = open_device(
					definition.device_id, **self._profile.open_kwargs(access))
			except Exception as exc:
				raise DEFwExecutionError(
					"failed to open the QDMI device session (MQT Core could "
					"not initialize it; device introspection requires an "
					f"initialized session): {exc}") from exc
			self._devices[cache_key] = device
		logging.debug("shim: QDMI device opened (%s)",
				self._profile.describe(access))
		return device

	def _ids(self):
		return (self._descriptor.get("provider", "iqm"),
				self._descriptor.get("id", "iqm-device"))

	# --- introspection facet: FoMaC Device -> qhw (section 13 milestone) ---

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
		# FoMaC exposes the device's live per-qubit coherence (T1/T2) and
		# per-gate fidelity; normalize them to qhw-calibration-v1. The record
		# names the calibration set it was read under when the device publishes
		# one. Selecting a *different* set is still a follow-up: the device
		# session always reflects the active one, so the argument is a no-op.
		provider, device_id = self._ids()
		cal = fomac_normalize.extract_calibration(
			self._device(),
			calibration_set_slot=self._profile.calibration_set_slot)
		return fomac_normalize.to_calibration_record(cal, provider, device_id)

	# --- execution: circuit -> provider program -> FoMaC submit_job ------

	def run_circuit(self, circuit):
		# The circuit arrives in a format this QPM declares, QPY or OpenQASM 2
		# (see util.circuit_payload). The profile encodes it as the program
		# its device library takes (IQM_JSON for QDMI-on-IQM, OpenQASM 3 for
		# Braket), then it is submitted through QDMI's FoMaC job interface,
		# polled to completion, and the counts are normalized to
		# qhw-result-v1 (the same record the QRMI path produces).
		# The reservation's credential, bound by the QPM controller
		# (UTIL_QPM attaches it before the circuit reaches the provider).
		credential = getattr(circuit, "provider_credential", None)
		with instrumentation.backend_phase("acquire"):
			device = self._device(credential=credential)
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
				self, source, info, device)

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
			# FoMaC exposes QDMI_JOB_PROPERTY_ID as a PROPERTY, not a method.
			# Calling it raised TypeError, which the except below then swallowed,
			# so job_id was always None and every QDMI result record lost the
			# provider job id -- leaving QDMI runs uncorrelatable with the
			# IQM-side job. QDMI-on-IQM does populate the property.
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
			self._last_job = {
				"id": job_id, "status": status, "cid": cid,
				"timing": timing, "shots": shots,
				"queue_position": queue_position}
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
		self._last_job = {
			"id": job_id, "status": "completed", "cid": cid,
			"timing": timing, "shots": shots,
			"queue_position": queue_position}
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
		# Poll FoMaC job.check() until a terminal state, and return
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

	# --- task timing / metadata (from the cached run_circuit job) -------

	def _last_job_for(self, cid):
		job = self._last_job
		if not job:
			raise DEFwExecutionError("QDMI has not run a circuit yet")
		if cid is not None and str(job.get("cid")) != str(cid):
			raise DEFwExecutionError(
				f"QDMI has no job for cid {cid!r} (last job cid "
				f"{job.get('cid')!r})")
		return job

	def get_task_timing(self, cid=None):
		job = self._last_job_for(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"queue_position": job.get("queue_position"),
			"timing": job.get("timing") or {}}

	def get_task_metadata(self, cid=None):
		job = self._last_job_for(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"shots": job.get("shots"),
			"queue_position": job.get("queue_position"),
			"backend": self._descriptor.get("provider", "iqm")}
