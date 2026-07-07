# QDMI driver — device-introspection facet, via QDMI's FoMaC query interface.
#
# QDMI is vendor-neutral: MQT Core's FoMaC API (mqt.core.fomac) exposes the
# device's sites (real qubit names + T1/T2), operations (loci + fidelity), and
# coupling map through QDMI's query interface. This driver reads that directly
# and normalizes it to the provider-neutral qhw schema (fomac_normalize) -- no
# Qiskit Target, no raw vendor data. QDMI is session-based and strong on device
# & calibration introspection, so this driver declares the introspection calls;
# execution/job calls stay with QRMI (the reservation owner) in the default
# wiring.
#
# Milestone status (design doc qpu-frontend-contract.md section 13):
# get_device_info / get_coupling_graph normalize the device topology, and
# get_calibration_snapshot reads the device's live per-qubit coherence (T1/T2)
# and per-gate fidelity through FoMaC -- all with the device's real qubit labels
# (e.g. "QB1"). get_backend_info / get_dynamic_backend_info stay with QRMI for
# now (their native shape carries raw IQM architecture data QDMI does not
# expose); binding them to QDMI's neutral model is a later milestone.

from .base_driver import BaseDriver
from . import fomac_normalize
from defw_exception import DEFwExecutionError
import json
import logging
import os
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
		"get_last_job_timing",
		"get_last_job_metadata",
	})

	def __init__(self, descriptor=None):
		# Per-resource descriptor (descriptor.py); carries device identity for
		# binding/creds and, later, dynamic capability discovery.
		self._descriptor = descriptor or {}
		self._device_obj = None
		self._last_job = None

	# --- QDMI session / device binding -------------------------------

	def _access(self):
		# Resolve connection settings for the QDMI device. Honor the same env
		# vars the native svc_iqm_qpm uses, then fall back to the shared
		# device-access config (util.device_access).
		provider = self._descriptor.get("provider", "iqm")
		device_id = self._descriptor.get("id")
		provider_device_id = (
			self._descriptor.get("provider_device_id")
			or self._descriptor.get("provider-device-id"))
		base_url = os.environ.get("QFW_QC_URL")
		token = os.environ.get("QFW_API_KEY")
		if not (base_url and token):
			try:
				from util.device_access import resolve_device_access
				cfg = resolve_device_access(provider=provider)
			except Exception as exc:
				raise DEFwExecutionError(
					"QDMI driver could not resolve device access for "
					f"provider {provider!r}: set QFW_QC_URL/QFW_API_KEY or "
					f"configure device access: {exc}") from exc
			base_url = base_url or cfg.get("url")
			token = token or cfg.get("api_key")
			device_id = device_id or cfg.get("device_id")
			provider_device_id = (
				provider_device_id
				or cfg.get("provider_device_id")
				or cfg.get("quantum_computer"))
		# The IQM QDMI library refuses to initialize a device session without a
		# base URL + token, and every device-property query then fails with a
		# bad-session-state error. Catch the missing credentials here so the
		# failure names what to set instead of surfacing deep inside FoMaC.
		missing = []
		if not base_url:
			missing.append("base URL (QFW_QC_URL or device-access url)")
		if not token:
			missing.append("API token (QFW_API_KEY or device-access api_key)")
		if missing:
			raise DEFwExecutionError(
				"QDMI driver cannot open a device session without " +
				" and ".join(missing))
		# Strip trailing slashes so URL construction can't produce "//" (the
		# IQM server rejects a doubled slash); keeps the base URL canonical.
		base_url = base_url.rstrip("/")
		return {
			"base_url": base_url,
			"token": token,
			"qc_alias": provider_device_id or device_id,
		}

	def _device(self):
		# Lazy: open the QDMI device through MQT Core's FoMaC loader once. Import
		# and construction are deferred so the service/Frontend build and route
		# even where the libraries are absent or credentials are unset; only a
		# real introspection call needs a live device. The IQM device library is
		# loaded by path with the "IQM" prefix; qc_alias is passed as the
		# device session's custom2 parameter (as iqm.qdmi.qiskit does).
		if self._device_obj is not None:
			return self._device_obj
		try:
			from iqm.qdmi._paths import IQM_QDMI_LIBRARY_PATH
			from mqt.core.fomac import add_dynamic_device_library
		except Exception as exc:
			raise DEFwExecutionError(
				"failed to import the QDMI FoMaC loader (mqt.core.fomac / "
				"iqm.qdmi). Install iqm-qdmi[qiskit] before using the QDMI "
				f"driver: {exc}") from exc
		access = self._access()
		# add_dynamic_device_library allocates the QDMI device session, applies
		# these parameters, and initializes it (the IQM library fetches the
		# device/calibration data during init). A query before a session is
		# initialized returns a bad-session-state error, so surface an init
		# failure here as exactly that: the session could not be opened.
		try:
			self._device_obj = add_dynamic_device_library(
				library_path=str(IQM_QDMI_LIBRARY_PATH),
				prefix="IQM",
				base_url=access.get("base_url"),
				token=access.get("token"),
				custom2=access.get("qc_alias"),
			)
		except Exception as exc:
			raise DEFwExecutionError(
				"failed to open the QDMI device session (FoMaC could not "
				"initialize it; device introspection requires an initialized "
				f"session): {exc}") from exc
		logging.debug("shim: QDMI device opened (%s)",
				access.get("qc_alias") or access.get("base_url"))
		return self._device_obj

	def _ids(self):
		return (self._descriptor.get("provider", "iqm"),
				self._descriptor.get("id", "iqm-device"))

	# --- introspection facet: FoMaC Device -> qhw (section 13 milestone) ---

	def get_device_info(self):
		provider, device_id = self._ids()
		topo = fomac_normalize.extract_topology(self._device())
		return fomac_normalize.to_device_record(topo, provider, device_id)

	def get_coupling_graph(self, calibration_set_id=None):
		provider, device_id = self._ids()
		topo = fomac_normalize.extract_topology(self._device())
		return fomac_normalize.to_coupling_record(topo, provider, device_id)

	def get_calibration_snapshot(self, calibration_set_id=None):
		# FoMaC exposes the device's live per-qubit coherence (T1/T2) and
		# per-gate fidelity; normalize them to qhw-calibration-v1. The device
		# session already reflects the active calibration set, so selecting a
		# specific calibration_set_id is a follow-up.
		provider, device_id = self._ids()
		cal = fomac_normalize.extract_calibration(self._device())
		return fomac_normalize.to_calibration_record(cal, provider, device_id)

	# --- execution: OpenQASM -> IQM circuit -> FoMaC submit_job ----------

	def run_circuit(self, circuit):
		# Canonical form is OpenQASM (circuit.info["qasm"]). Transcode it to an
		# IQM circuit with the shared util, then submit through QDMI's FoMaC job
		# interface as an IQM_JSON program. Note the QRMI/QDMI difference: QDMI's
		# IQM_JSON program is a SINGLE circuit -- QDMI-on-IQM wraps it into the
		# run request (circuits/shots/calibration_set) itself -- whereas QRMI
		# submits the whole run request. Poll to completion and normalize the
		# counts to qhw-result-v1 (the same record the QRMI path produces).
		device = self._device()
		info = getattr(circuit, "info", None) or {}
		cid = circuit.get_cid() if hasattr(circuit, "get_cid") else info.get("cid")
		qasm = info.get("qasm")
		if not qasm:
			raise DEFwExecutionError(
				"QDMI run_circuit requires OpenQASM in circuit info['qasm']")
		shots = int(info.get("num_shots", info.get("shots", 1024)))
		mapping = info.get("iqm_qubit_mapping") or info.get("qubit_mapping")
		timeout = float(info.get("timeout", 300.0))
		poll = float(info.get("poll_interval", 1.0))
		provider, device_id = self._ids()

		# The transcode needs the device's active qubits; FoMaC sites supply
		# them (QDMI has no raw dynamic-architecture dict like QRMI's target()).
		from util.iqm_transcode import build_iqm_circuit
		topo = fomac_normalize.extract_topology(device)
		dynamic = {"qubits": topo.get("qubits") or []}
		iqm_circuit = build_iqm_circuit(qasm, dynamic, mapping)
		program = self._serialize_program(iqm_circuit)

		try:
			from mqt.core.fomac import ProgramFormat
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to import mqt.core.fomac ProgramFormat: {exc}") from exc

		timing = {}
		start = time.monotonic()
		try:
			job = device.submit_job(program, ProgramFormat.IQM_JSON, int(shots))
		except Exception as exc:
			raise DEFwExecutionError(f"QDMI submit_job failed: {exc}") from exc
		timing["submit_seconds"] = time.monotonic() - start

		status = self._poll_job(job, timeout, poll)
		timing["wait_seconds"] = (
			time.monotonic() - start - timing["submit_seconds"])
		try:
			job_id = job.id()
		except Exception:
			job_id = None
		if status != "completed":
			self._last_job = {
				"id": job_id, "status": status, "cid": cid,
				"timing": timing, "shots": shots}
			raise DEFwExecutionError(
				f"QDMI job {job_id} finished with status {status!r}")

		result_started = time.monotonic()
		try:
			counts = job.get_counts()
		except Exception as exc:
			raise DEFwExecutionError(f"QDMI get_counts failed: {exc}") from exc
		timing["result_fetch_seconds"] = time.monotonic() - result_started
		timing["total_wall_seconds"] = time.monotonic() - start

		record = fomac_normalize.to_result_record(
			counts, shots, provider, device_id, job_id=job_id,
			status="completed")
		self._last_job = {
			"id": job_id, "status": "completed", "cid": cid,
			"timing": timing, "shots": shots}
		return record

	def _serialize_program(self, iqm_circuit):
		# Serialize the transcoded IQM circuit to the single-circuit JSON QDMI's
		# IQM_JSON program expects. Prefer iqm-client's canonical serializer;
		# fall back to a generic coercion. Validated against the live IQM circuit
		# schema on hardware.
		from util.iqm_transcode import to_jsonable
		try:
			try:
				from iqm.iqm_client.util import to_json_dict
				payload = to_json_dict(iqm_circuit)
			except ImportError:
				payload = to_jsonable(iqm_circuit)
			return json.dumps(payload)
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to serialize the IQM circuit for QDMI: {exc}") from exc

	def _poll_job(self, job, timeout, poll):
		# Poll FoMaC job.check() until a terminal state; returns
		# completed/failed/cancelled (or raises on timeout).
		deadline = time.monotonic() + max(timeout, 0.0)
		while True:
			try:
				state = _job_status(job.check())
			except Exception as exc:
				raise DEFwExecutionError(
					f"QDMI job.check() failed: {exc}") from exc
			if state == "done":
				return "completed"
			if state == "failed":
				return "failed"
			if state in ("canceled", "cancelled"):
				return "cancelled"
			if time.monotonic() >= deadline:
				raise DEFwExecutionError(
					f"QDMI job timed out after {timeout}s (status {state!r})")
			time.sleep(max(poll, 0.0))

	# --- last-job timing / metadata (from the cached run_circuit job) ----

	def _last_job_for(self, cid):
		job = self._last_job
		if not job:
			raise DEFwExecutionError("QDMI has not run a circuit yet")
		if cid is not None and str(job.get("cid")) != str(cid):
			raise DEFwExecutionError(
				f"QDMI has no job for cid {cid!r} (last job cid "
				f"{job.get('cid')!r})")
		return job

	def get_last_job_timing(self, cid=None):
		job = self._last_job_for(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"timing": job.get("timing") or {}}

	def get_last_job_metadata(self, cid=None):
		job = self._last_job_for(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"shots": job.get("shots"),
			"backend": self._descriptor.get("provider", "iqm")}
