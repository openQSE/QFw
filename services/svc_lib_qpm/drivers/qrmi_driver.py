# QRMI driver — execution/reservation owner that ALSO serves device
# introspection.
#
# QRMI (IBM's Rust + C + Python resource-management interface; the `qrmi`
# package) owns the reservation lifecycle, so it is the default execution
# owner. It also exposes the device target: `QuantumResource.target()` returns
# the device's RAW IQM data (for IQM: dynamic_quantum_architecture,
# calibration_set, quality_metrics). So introspection is NOT QDMI-exclusive —
# it is a composable facet QRMI serves too.
#
# Because that payload is the same IQM data the native svc_iqm_qpm path handles,
# this driver reuses `qhw-iqm` to normalize it to qhw — rather than a separate
# adapter. (QDMI is different: it presents a vendor-neutral query interface, so
# the QDMI driver reads device info via MQT Core's FoMaC API and normalizes it
# with fomac_normalize.) target() is not reservation-bound, so introspection works
# without acquire().
#
# Milestone status (design doc qpu-frontend-contract.md section 13): the whole
# introspection facet is wired from one cached target() payload —
# get_device_info / get_coupling_graph (-> qhw-iqm device/coupling),
# get_calibration_snapshot (-> qhw-iqm calibration), get_dynamic_backend_info
# (the dynamic architecture, native shape), and get_backend_info (native
# composite with an embedded qhw device). Execution remains stubbed for a later
# milestone.

from .base_driver import BaseDriver
from defw_exception import DEFwExecutionError
import json
import logging
import os
import time


def _status_str(status):
	# QRMI task_status returns a TaskStatus enum; reduce it to a lowercase
	# string regardless of whether the binding exposes .name/.value/repr.
	for attr in ("name", "value"):
		value = getattr(status, attr, None)
		if value is not None:
			return str(value).lower()
	return str(status).lower()


class QrmiDriver(BaseDriver):
	name = "qrmi"
	CAPABILITIES = frozenset({
		"get_device_info",          # target() -> qhw-iqm device
		"get_coupling_graph",       # target() -> qhw-iqm coupling
		"get_calibration_snapshot", # target() -> qhw-iqm calibration
		"get_dynamic_backend_info", # target() -> dynamic architecture
		"get_backend_info",         # target() -> native composite + qhw device
		"run_circuit",
		"get_last_job_timing",
		"get_last_job_metadata",
	})

	def __init__(self, descriptor=None):
		# Per-resource descriptor (descriptor.py); carries device identity for
		# binding/creds and, later, dynamic capability discovery.
		self._descriptor = descriptor or {}
		self._qrmi = None
		self._resource_objs = {}
		self._target_cache = {}
		self._last_job = None

	def _resource(self):
		# Lazy import so the service/Frontend construct and route even where
		# the qrmi package is not importable; only a real call needs it.
		if self._qrmi is None:
			try:
				import qrmi
			except Exception as exc:
				raise DEFwExecutionError(
					"failed to import qrmi. Install the qrmi Python package "
					f"before using the QRMI driver: {exc}") from exc
			self._qrmi = qrmi
			logging.debug("shim: QRMI client initialized")
		return self._qrmi

	# --- QRMI resource binding ------------------------------------------

	def _qc_alias(self, credential=None):
		credential = dict(credential or {})
		value = (
			credential.get("provider_device_id")
			or credential.get("quantum_computer")
			or self._descriptor.get("provider_device_id")
			or self._descriptor.get("provider-device-id"))
		if value:
			return value
		try:
			access = self._access(credential=credential)
		except Exception:
			return self._descriptor.get("id")
		return (
			access.get("provider_device_id")
			or access.get("quantum_computer")
			or self._descriptor.get("id"))

	def _access(self, credential=None):
		# Resolve the IQM endpoint + token for the QRMI resource. Honor the same
		# env vars the native svc_iqm_qpm uses, then fall back to the shared
		# device-access config (util.device_access). Mirrors QdmiDriver._access.
		credential = dict(credential or {})
		provider = self._descriptor.get("provider", "iqm")
		base_url = credential.get("url") or os.environ.get("QFW_QC_URL")
		token = (
			credential.get("api_key") or
			credential.get("token") or
			os.environ.get("QFW_API_KEY"))
		provider_device_id = (
			credential.get("provider_device_id")
			or credential.get("quantum_computer")
			or self._descriptor.get("provider_device_id")
			or self._descriptor.get("provider-device-id"))
		if not (base_url and token):
			try:
				from util.device_access import resolve_device_access
				cfg = resolve_device_access(
					provider=provider,
					device_id=credential.get("device_id"),
					user=credential.get("user"),
					credential_hint=credential.get("credential_hint"),
					credential_handle=credential.get("credential_handle"))
			except Exception as exc:
				raise DEFwExecutionError(
					"QRMI driver could not resolve IQM device access for "
					f"provider {provider!r}: set QFW_QC_URL/QFW_API_KEY or "
					f"configure device access: {exc}") from exc
			base_url = base_url or cfg.get("url")
			token = token or cfg.get("api_key")
			provider_device_id = (
				provider_device_id
				or cfg.get("provider_device_id")
				or cfg.get("quantum_computer"))
		# Strip trailing slashes: QRMI's IQM client builds URLs as
		# f"{endpoint}/api/v1/...", so a configured base URL ending in "/"
		# yields "//api/v1/..." which the IQM server rejects (empty target).
		if base_url:
			base_url = base_url.rstrip("/")
		return {
			"base_url": base_url,
			"token": token,
			"provider_device_id": provider_device_id,
			"quantum_computer": provider_device_id,
		}

	def _ensure_iqm_isa_env(self, alias, credential=None):
		# QRMI's IQM resource reads its endpoint/token from
		# {backend}_QRMI_IQM_ISA_ENDPOINT / {backend}_QRMI_IQM_ISA_TOKEN at
		# construction (IQMServer::new). Inside a SLURM reservation the SPANK
		# plugin populates these; outside one (e.g. a bare introspection call)
		# they are unset and QuantumResource() fails before target() ever runs.
		# Resolve them from device-access config and export whichever is missing
		# -- never overriding values the SPANK plugin already set. QRMI keys the
		# env vars by the resource id up to the first comma
		# (backend_name,calibration_set_id), so match that prefix here.
		backend = alias.split(",")[0]
		endpoint_var = f"{backend}_QRMI_IQM_ISA_ENDPOINT"
		token_var = f"{backend}_QRMI_IQM_ISA_TOKEN"
		if credential:
			access = self._access(credential=credential)
			if access.get("base_url"):
				os.environ[endpoint_var] = access["base_url"]
			if access.get("token"):
				os.environ[token_var] = access["token"]
			return
		if os.environ.get(endpoint_var) and os.environ.get(token_var):
			return
		access = self._access()
		if not os.environ.get(endpoint_var) and access.get("base_url"):
			os.environ[endpoint_var] = access["base_url"]
		if not os.environ.get(token_var) and access.get("token"):
			os.environ[token_var] = access["token"]
		missing = [v for v in (endpoint_var, token_var)
				if not os.environ.get(v)]
		if missing:
			raise DEFwExecutionError(
				"QRMI IQM introspection needs " + " and ".join(missing) +
				"; set them, or set QFW_QC_URL/QFW_API_KEY, or configure "
				"device access (these are normally injected by the SPANK "
				"plugin inside a reservation)")

	def _qpu(self, credential=None):
		# Lazy: open the QRMI QuantumResource for this resource's IQM server.
		# QRMI reads its credentials/config from the environment; target() is not
		# reservation-bound, so introspection works without acquire() as long as
		# the endpoint/token env vars are present (_ensure_iqm_isa_env supplies
		# them from device-access config when no reservation has).
		cache_key = self._credential_cache_key(credential)
		if cache_key in self._resource_objs:
			return self._resource_objs[cache_key]
		qrmi = self._resource()
		alias = self._qc_alias(credential=credential)
		if not alias:
			raise DEFwExecutionError(
				"QRMI introspection needs a QFw device id; set "
				"QFW_QPU_DEVICE_ID or configure a device descriptor")
		self._ensure_iqm_isa_env(alias, credential=credential)
		try:
			resource_obj = qrmi.QuantumResource(
					alias, qrmi.ResourceType.IQMServer)
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to open QRMI IQM resource {alias!r}: {exc}") from exc
		self._resource_objs[cache_key] = resource_obj
		logging.debug("shim: QRMI IQM resource opened (%s)", alias)
		return resource_obj

	def _credential_cache_key(self, credential=None):
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

	def _target(self, credential=None):
		# QRMI target() is a remote call returning raw IQM JSON (dynamic
		# architecture / calibration_set / quality_metrics). Parse it once per
		# driver instance and serve every introspection call from the cache, so
		# the four calls don't each re-fetch the same payload.
		cache_key = self._credential_cache_key(credential)
		if cache_key not in self._target_cache:
			try:
				self._target_cache[cache_key] = json.loads(
					self._qpu(credential=credential).target().value)
			except Exception as exc:
				raise DEFwExecutionError(
					f"failed to read QRMI target(): {exc}") from exc
		return self._target_cache[cache_key]

	def _arch_raw(self):
		# Map target() to the {static_architecture, dynamic_architecture} shape
		# qhw-iqm's device/coupling normalizers expect.
		target = self._target()
		raw = {"dynamic_architecture":
				target.get("dynamic_quantum_architecture") or {}}
		static = target.get("static_quantum_architecture")
		if static:
			raw["static_architecture"] = static
		return raw

	def _device_id(self):
		return self._descriptor.get("id", "iqm-device")

	# --- introspection facet: reuse qhw-iqm on QRMI's raw IQM data ------

	def get_device_info(self):
		from qhw_iqm import normalize_device
		return normalize_device(self._arch_raw(), device_id=self._device_id())

	def get_coupling_graph(self, calibration_set_id=None):
		from qhw_iqm import normalize_coupling
		return normalize_coupling(self._arch_raw(), device_id=self._device_id())

	def get_calibration_snapshot(self, calibration_set_id=None):
		# target() carries the IQM calibration_set + quality_metrics; feed them
		# (with the dynamic architecture) to qhw-iqm — the same normalizer the
		# native svc_iqm_qpm path uses — to build a qhw-calibration-v1 record.
		# Selecting a specific calibration_set_id is a follow-up; this returns
		# the resource's current/default calibration.
		from qhw_iqm import normalize_calibration
		target = self._target()
		raw = {
			"dynamic_architecture":
				target.get("dynamic_quantum_architecture") or {},
			"calibration_set": target.get("calibration_set") or {},
			"quality_metric_set": target.get("quality_metrics") or {},
		}
		return normalize_calibration(raw, device_id=self._device_id())

	def get_dynamic_backend_info(self, calibration_set_id=None):
		# The dynamic architecture as-is, matching the native svc_iqm_qpm shape
		# (a provider dict, not a qhw record).
		return {
			"backend": self._descriptor.get("provider", "iqm"),
			"metadata_supported": True,
			"dynamic_architecture":
				self._target().get("dynamic_quantum_architecture") or {},
		}

	def get_backend_info(self):
		# Native composite shape (mirrors svc_iqm_qpm.get_backend_info): the
		# provider fields plus an embedded qhw device record. QRMI's target()
		# does not carry the static architecture, so that field is present only
		# when the resource reports it.
		from qhw_iqm import normalize_device
		target = self._target()
		dynamic = target.get("dynamic_quantum_architecture") or {}
		return {
			"backend": self._descriptor.get("provider", "iqm"),
			"metadata_supported": True,
			"static_architecture":
				target.get("static_quantum_architecture") or {},
			"active_qubits": dynamic.get("qubits") or [],
			"calibration_set_id": dynamic.get("calibration_set_id"),
			"qhw_device": normalize_device(
					self._arch_raw(), device_id=self._device_id()),
		}

	# --- execution: OpenQASM -> IQM JSON -> QRMI task lifecycle ----------

	def run_circuit(self, circuit):
		# Canonical form is OpenQASM (circuit.info["qasm"]). Transcode it to an
		# IQM circuit with the shared util, submit through QRMI's task lifecycle,
		# poll to completion, and normalize the counts to qhw-result-v1 (the same
		# normalizer the native svc_iqm_qpm path uses). QRMI-for-IQM has no
		# acquire/release, so there is no reservation step.
		qrmi = self._resource()
		info = getattr(circuit, "info", None) or {}
		credential = getattr(circuit, "provider_credential", None)
		cid = circuit.get_cid() if hasattr(circuit, "get_cid") else info.get("cid")
		qasm = info.get("qasm")
		if not qasm:
			raise DEFwExecutionError(
				"QRMI run_circuit requires OpenQASM in circuit info['qasm']")
		shots = int(info.get("num_shots", info.get("shots", 1024)))
		mapping = info.get("iqm_qubit_mapping") or info.get("qubit_mapping")
		use_timeslot = bool(info.get("use_timeslot", False))
		timeout = float(info.get("timeout", 300.0))
		poll = float(info.get("poll_interval", 1.0))

		target = self._target(credential=credential)
		dynamic = target.get("dynamic_quantum_architecture") or {}
		calibration_set_id = (
			info.get("calibration_set_id")
			or info.get("iqm_calibration_set_id")
			or dynamic.get("calibration_set_id"))

		from util.iqm_transcode import build_iqm_circuit
		iqm_circuit = build_iqm_circuit(qasm, dynamic, mapping)
		iqmjson, run_request = self._build_iqmjson(
				iqm_circuit, shots, calibration_set_id)

		payload = qrmi.Payload.IQMServer(
			iqmjson=iqmjson, job_type="circuit",
			use_timeslot=use_timeslot, tag=None)

		timing = {}
		start = time.monotonic()
		try:
			job_id = self._qpu(credential=credential).task_start(payload)
		except Exception as exc:
			raise DEFwExecutionError(
				f"QRMI task_start failed: {exc}") from exc
		timing["submit_seconds"] = time.monotonic() - start

		status = self._poll_task(job_id, timeout, poll, credential=credential)
		timing["wait_seconds"] = (
			time.monotonic() - start - timing["submit_seconds"])
		if status != "completed":
			self._last_job = {
				"id": str(job_id), "status": status, "cid": cid,
				"timing": timing, "shots": shots}
			raise DEFwExecutionError(
				f"QRMI job {job_id} finished with status {status!r}")

		result_started = time.monotonic()
		try:
			result_json = json.loads(
				self._qpu(credential=credential).task_result(job_id).value)
		except Exception as exc:
			raise DEFwExecutionError(
				f"QRMI task_result failed: {exc}") from exc
		timing["result_fetch_seconds"] = time.monotonic() - result_started
		timing["total_wall_seconds"] = time.monotonic() - start

		measurement_counts = result_json.get("measurement_counts")
		circuits = run_request.get("circuits") if isinstance(
				run_request, dict) else None
		raw = {
			"job": {"id": str(job_id), "status": "completed"},
			"run_request": run_request if isinstance(run_request, dict) else {},
			"measurement_counts": measurement_counts,
			"circuits": circuits or [],
		}
		from qhw_iqm import normalize_result
		record = normalize_result(raw, device_id=self._device_id())

		self._last_job = {
			"id": str(job_id), "status": "completed", "cid": cid,
			"timing": timing, "shots": shots,
			"measurements": result_json.get("measurements")}
		return record

	def _build_iqmjson(self, iqm_circuit, shots, calibration_set_id):
		# Build an iqm-client RunRequest the same way QRMI's own Qiskit adapter
		# (qrmi.qiskit_iqm) does, then serialize it: QRMI's task_start expects the
		# RunRequest JSON as the IQM Server job body. This is the QASM -> IQM JSON
		# step; the exact RunRequest schema is validated against the live IQM
		# Server on hardware. Returns (json_str, parsed_dict).
		try:
			from iqm.iqm_client import CircuitCompilationOptions
			from iqm.station_control.interface.models import _build_run_request
		except Exception as exc:
			raise DEFwExecutionError(
				"iqm-client is required to build the IQM job payload for QRMI "
				f"execution: {exc}") from exc
		calset = calibration_set_id
		if calset is not None and not isinstance(calset, str):
			calset = str(calset)
		try:
			run_request = _build_run_request(
				[iqm_circuit],
				calibration_set_id=calset,
				shots=int(shots),
				options=CircuitCompilationOptions())
			iqmjson = run_request.model_dump_json()
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to build the IQM run-request JSON for QRMI: {exc}") \
				from exc
		return iqmjson, json.loads(iqmjson)

	def _poll_task(self, job_id, timeout, poll, credential=None):
		# Poll QRMI task_status until a terminal state; returns
		# completed/failed/cancelled (or raises on timeout).
		deadline = time.monotonic() + max(timeout, 0.0)
		while True:
			try:
				raw = self._qpu(credential=credential).task_status(job_id)
			except Exception as exc:
				raise DEFwExecutionError(
					f"QRMI task_status failed: {exc}") from exc
			state = _status_str(raw)
			if "complet" in state:
				return "completed"
			if "fail" in state or "error" in state:
				return "failed"
			if "cancel" in state:
				return "cancelled"
			if time.monotonic() >= deadline:
				raise DEFwExecutionError(
					f"QRMI job {job_id} timed out after {timeout}s "
					f"(last status {state!r})")
			time.sleep(max(poll, 0.0))

	# --- last-job timing / metadata (from the cached run_circuit job) ----

	def _last_job_for(self, cid):
		job = self._last_job
		if not job:
			raise DEFwExecutionError("QRMI has not run a circuit yet")
		if cid is not None and str(job.get("cid")) != str(cid):
			raise DEFwExecutionError(
				f"QRMI has no job for cid {cid!r} (last job cid "
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
