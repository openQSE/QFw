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
		self._resource_obj = None
		self._target_cache = None

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

	def _qc_alias(self):
		# IQM resource alias for QuantumResource. Honor the env var the native
		# svc_iqm_qpm uses, else the shared device-access config.
		alias = os.environ.get("QFW_IQM_QUANTUM_COMPUTER")
		if alias:
			return alias
		try:
			from util.device_access import resolve_device_access
			cfg = resolve_device_access(
					provider=self._descriptor.get("provider", "iqm"))
			return cfg.get("quantum_computer")
		except Exception:
			return None

	def _access(self):
		# Resolve the IQM endpoint + token for the QRMI resource. Honor the same
		# env vars the native svc_iqm_qpm uses, then fall back to the shared
		# device-access config (util.device_access). Mirrors QdmiDriver._access.
		provider = self._descriptor.get("provider", "iqm")
		base_url = os.environ.get("QFW_QC_URL")
		token = os.environ.get("QFW_API_KEY")
		if not (base_url and token):
			try:
				from util.device_access import resolve_device_access
				cfg = resolve_device_access(provider=provider)
			except Exception as exc:
				raise DEFwExecutionError(
					"QRMI driver could not resolve IQM device access for "
					f"provider {provider!r}: set QFW_QC_URL/QFW_API_KEY or "
					f"configure device access: {exc}") from exc
			base_url = base_url or cfg.get("url")
			token = token or cfg.get("api_key")
		return {"base_url": base_url, "token": token}

	def _ensure_iqm_isa_env(self, alias):
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

	def _qpu(self):
		# Lazy: open the QRMI QuantumResource for this resource's IQM server.
		# QRMI reads its credentials/config from the environment; target() is not
		# reservation-bound, so introspection works without acquire() as long as
		# the endpoint/token env vars are present (_ensure_iqm_isa_env supplies
		# them from device-access config when no reservation has).
		if self._resource_obj is not None:
			return self._resource_obj
		qrmi = self._resource()
		alias = self._qc_alias()
		if not alias:
			raise DEFwExecutionError(
				"QRMI introspection needs an IQM resource alias; set "
				"QFW_IQM_QUANTUM_COMPUTER or configure device access")
		self._ensure_iqm_isa_env(alias)
		try:
			self._resource_obj = qrmi.QuantumResource(
					alias, qrmi.ResourceType.IQMServer)
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to open QRMI IQM resource {alias!r}: {exc}") from exc
		logging.debug("shim: QRMI IQM resource opened (%s)", alias)
		return self._resource_obj

	def _target(self):
		# QRMI target() is a remote call returning raw IQM JSON (dynamic
		# architecture / calibration_set / quality_metrics). Parse it once per
		# driver instance and serve every introspection call from the cache, so
		# the four calls don't each re-fetch the same payload.
		if self._target_cache is None:
			try:
				self._target_cache = json.loads(self._qpu().target().value)
			except Exception as exc:
				raise DEFwExecutionError(
					f"failed to read QRMI target(): {exc}") from exc
		return self._target_cache

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

	# --- execution + metadata (routing wired; binding to qrmi is a later
	#     milestone — docs/qpu-frontend-contract.md section 13) -----------

	def run_circuit(self, circuit):
		self._resource()
		return self._pending("run_circuit", "qrmi")

	def get_last_job_timing(self, cid=None):
		self._resource()
		return self._pending("get_last_job_timing", "qrmi")

	def get_last_job_metadata(self, cid=None):
		self._resource()
		return self._pending("get_last_job_metadata", "qrmi")
