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
# composite with an embedded qhw device). Circuit execution uses QRMI's task
# lifecycle and returns normalized qhw results.

from .base_driver import BaseDriver
from defw_exception import (DEFwExecutionError, DEFwNotFound,
		DEFwNotReady)
import json
import logging
import os
import time


# QRMI 0.24.0 classifies its failures instead of raising one opaque
# RuntimeError, so a caller can finally tell "no such backend" from
# "credentials rejected" from "that job is gone" without matching on message
# text. This maps the classes that have an honest DEFw counterpart.
#
# QRMI 0.24.4 added default trait implementations: a vendor now overrides only
# what its backend supports and the rest raise UnsupportedFunction. That has a
# precise counterpart here. NotImplementedByLibrary is the shim's own
# gap-map signal, raised by the Frontend when no wired library serves a call,
# so a library saying "I do not implement this" is the same statement arriving
# from the other direction.
#
# Mapping it is worth more than tidiness. The Frontend routes from a
# hand-maintained capability map, and that map can disagree with the libraries
# it describes. Before 0.24.4 that disagreement was invisible on this path:
# acquire() on IQM returned a plausible UUID and the caller could not tell.
# Now a library that does not implement a call says so, and translating it to
# NotImplementedByLibrary means the descriptor claiming otherwise surfaces as
# the gap it is, in the vocabulary the rest of the shim already uses, rather
# than as a generic execution error a reader has to interpret.
#
# Only three of the remaining kinds have an honest DEFw counterpart. DEFw has
# no authentication, bad-input or configuration error,
# and inventing a mapping onto a DEFw type that means something else would make
# the type less trustworthy than leaving it alone -- so everything else stays
# DEFwExecutionError. The QRMI class name goes into every message either way,
# which keeps the distinction greppable even where the DEFw type cannot carry
# it.
#
# Looked up by name at raise time, so a qrmi too old to define these (anything
# before 0.24.0) simply never matches and every failure stays
# DEFwExecutionError, exactly as before.
# QRMI fronts several vendors behind one interface, and ResourceType selects
# which backend a QuantumResource actually opens. Some providers publish more
# than one, so a descriptor may name the type explicitly with resource-type;
# a provider serving exactly one resolves on its own.
#
# An ambiguous provider is an error rather than a guess. Picking the wrong type
# does not fail here, it fails much later as an authentication error against
# the wrong endpoint, which is a bad trade for saving the user one config line.
#
# Names are resolved against the installed qrmi at call time rather than
# imported, so a qrmi that does not carry one of these reports it as an
# unknown type instead of failing at import.
# QRMI names each IBM service's variables after the service, so the resource
# type selects the family: {backend}_QRMI_IBM_<kind>_ENDPOINT and friends.
IBM_RESOURCE_ENV_KINDS = {
	"IBMQiskitRuntimeService": "QRS",
	"IBMQuantumComputeService": "QCS",
	"IBMQuantumSystem": "QS",
}

# IBM Cloud's IAM endpoint. QRMI trades the API key for a token there, with
# grant_type urn:ibm:params:oauth:grant-type:apikey against /identity/token,
# and that is the same host for every IBM service. Only a non-public IBM Cloud
# needs it overridden, so it defaults rather than being required.
IBM_DEFAULT_IAM_ENDPOINT = "https://iam.cloud.ibm.com"

PROVIDER_RESOURCE_TYPES = {
	"iqm": ("IQMServer",),
	"ibm": ("IBMQiskitRuntimeService", "IBMQuantumComputeService",
		"IBMQuantumSystem"),
	"pasqal": ("PasqalCloud", "PasqalLocal"),
	"alicebob": ("AliceBobFelis",),
}


def _not_implemented_by_library():
	# Imported at call time, not at module scope. drivers/__init__ is imported
	# while the svc_lib_qpm package is still initializing, so reaching up to a
	# sibling module from here at import time couples this driver to that
	# ordering for no benefit. The class is only ever needed to build an
	# exception that is about to be raised.
	from ..frontend import NotImplementedByLibrary
	return NotImplementedByLibrary


_QRMI_ERROR_MAP = (
	("UnsupportedFunctionError", _not_implemented_by_library),
	("ResourceNotFoundError", DEFwNotFound),
	("TaskNotFoundError", DEFwNotFound),
	("TaskNotReadyError", DEFwNotReady),
)


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
		"get_device_info",           # target() -> qhw-iqm device
		"get_coupling_graph",        # target() -> qhw-iqm coupling
		"get_calibration_snapshot",  # target() -> qhw-iqm calibration
		"get_dynamic_backend_info",  # target() -> dynamic architecture
		"get_backend_info",          # target() -> native composite + qhw device
		"run_circuit",
		"get_task_timing",
		"get_task_metadata",
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

	def _qrmi_error(self, exc, context):
		# Translate a QRMI failure into the closest DEFw exception (see
		# _QRMI_ERROR_MAP). Returns the exception rather than raising it so the
		# call site keeps its `raise ... from exc` and the original traceback.
		qrmi = self._qrmi
		# Naming the QRMI class beats a prose label: it never duplicates the
		# message QRMI already wrote, and it is what upstream documents.
		message = f"{context}: [{type(exc).__name__}] {exc}"
		if qrmi is not None:
			for attr, defw_cls in _QRMI_ERROR_MAP:
				cls = getattr(qrmi, attr, None)
				if cls is not None and isinstance(exc, cls):
					# A callable entry defers resolving the DEFw class (see
					# _not_implemented_by_library); a class entry is used as is.
					if not isinstance(defw_cls, type):
						defw_cls = defw_cls()
					return defw_cls(message)
		return DEFwExecutionError(message)

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
					"QRMI driver could not resolve device access for "
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

	def _resource_type(self, qrmi):
		# Resolve the ResourceType this descriptor should open. Returns the
		# name alongside the value so errors and logs can say which backend
		# was attempted.
		name = (self._descriptor.get("resource_type")
			or self._descriptor.get("resource-type"))
		provider = str(self._descriptor.get("provider") or "iqm").lower()
		if not name:
			candidates = PROVIDER_RESOURCE_TYPES.get(provider, ())
			if len(candidates) == 1:
				name = candidates[0]
			elif candidates:
				raise DEFwExecutionError(
					f"provider {provider!r} serves more than one QRMI "
					"resource type; set resource-type on the device "
					"descriptor to one of: " + ", ".join(candidates))
			else:
				raise DEFwExecutionError(
					"no QRMI resource type is known for provider "
					f"{provider!r}; set resource-type on the device "
					"descriptor")
		resource_type = getattr(qrmi.ResourceType, str(name), None)
		if resource_type is None:
			available = ", ".join(sorted(
				item for item in dir(qrmi.ResourceType)
				if not item.startswith("_")))
			raise DEFwExecutionError(
				f"unknown QRMI resource type {str(name)!r}; the installed "
				f"qrmi serves: {available}")
		return str(name), resource_type

	def _ensure_resource_env(self, type_name, alias, credential=None):
		# Each resource type reads its own {backend}_QRMI_* variables. Populate
		# the families we can resolve; Pasqal and Alice & Bob have no
		# device-access mapping yet and rely on the environment already
		# carrying theirs, from the SPANK plugin or the operator.
		if type_name == "IQMServer":
			self._ensure_iqm_isa_env(alias, credential=credential)
			return
		kind = IBM_RESOURCE_ENV_KINDS.get(type_name)
		if kind:
			self._ensure_ibm_env(kind, alias, credential=credential)

	def _ensure_ibm_env(self, kind, alias, credential=None):
		# QRMI's IBM resources read endpoint, IAM endpoint, API key and service
		# CRN at construction, so resolve whatever is missing before opening
		# one. Never override what is already set: inside a reservation the
		# SPANK plugin owns these.
		#
		# The endpoint and API key come from device-access config, the same
		# source the IQM path uses. The service CRN and the IAM endpoint have
		# no field in that config (#59 blocker 3), so they come from the
		# environment, mirroring how _access falls back to QFW_QC_URL and
		# QFW_API_KEY. That makes them process-wide rather than per-device,
		# which is a real limitation and the reason blocker 3 still matters.
		backend = alias.split(",")[0]
		prefix = f"{backend}_QRMI_IBM_{kind}"
		endpoint_var = f"{prefix}_ENDPOINT"
		iam_endpoint_var = f"{prefix}_IAM_ENDPOINT"
		apikey_var = f"{prefix}_IAM_APIKEY"
		crn_var = f"{prefix}_SERVICE_CRN"

		if not (os.environ.get(endpoint_var) and os.environ.get(apikey_var)):
			try:
				access = self._access(credential=credential)
			except DEFwExecutionError:
				# Fall through to the missing-variable report below, which
				# names what to set. It is more actionable than a failure to
				# resolve device access the caller may not be relying on.
				access = {}
			if not os.environ.get(endpoint_var) and access.get("base_url"):
				os.environ[endpoint_var] = access["base_url"]
			if not os.environ.get(apikey_var) and access.get("token"):
				os.environ[apikey_var] = access["token"]

		if not os.environ.get(iam_endpoint_var):
			iam_endpoint = (os.environ.get("QFW_IBM_IAM_ENDPOINT")
				or IBM_DEFAULT_IAM_ENDPOINT)
			os.environ[iam_endpoint_var] = iam_endpoint

		crn = os.environ.get("QFW_IBM_SERVICE_CRN")
		if crn and not os.environ.get(crn_var):
			os.environ[crn_var] = crn

		# Object storage applies only to IBMQuantumSystem, which stages results
		# through a bucket. No config field carries these either, and the other
		# IBM services never read them, so they are environment-only and stay
		# unset when absent rather than being required here.
		if kind == "QS":
			for suffix, source in (
					("S3_ENDPOINT", "QFW_IBM_S3_ENDPOINT"),
					("S3_BUCKET", "QFW_IBM_S3_BUCKET"),
					("S3_REGION", "QFW_IBM_S3_REGION"),
					("AWS_ACCESS_KEY_ID", "QFW_IBM_AWS_ACCESS_KEY_ID"),
					("AWS_SECRET_ACCESS_KEY",
						"QFW_IBM_AWS_SECRET_ACCESS_KEY")):
				value = os.environ.get(source)
				name = f"{prefix}_{suffix}"
				if value and not os.environ.get(name):
					os.environ[name] = value

		missing = [name for name in (
			endpoint_var, iam_endpoint_var, apikey_var, crn_var)
			if not os.environ.get(name)]
		if missing:
			raise DEFwExecutionError(
				"QRMI IBM access needs " + " and ".join(missing) +
				"; the endpoint and API key come from device-access config or "
				"QFW_QC_URL/QFW_API_KEY, and the service CRN from "
				"QFW_IBM_SERVICE_CRN (no device-access field carries a CRN "
				"yet, see openQSE/QFw#59). Inside a reservation the SPANK "
				"plugin normally supplies all of these")

	def _qpu(self, credential=None):
		# Lazy: open the QRMI QuantumResource this descriptor names. QRMI reads
		# its credentials/config from the environment; target() is not
		# reservation-bound, so introspection works without acquire() as long as
		# the resource type's env vars are present (_ensure_resource_env
		# supplies the IQM ones from device-access config when no reservation
		# has).
		cache_key = self._credential_cache_key(credential)
		if cache_key in self._resource_objs:
			return self._resource_objs[cache_key]
		qrmi = self._resource()
		alias = self._qc_alias(credential=credential)
		if not alias:
			raise DEFwExecutionError(
				"QRMI introspection needs a QFw device id; set "
				"QFW_QPU_DEVICE_ID or configure a device descriptor")
		type_name, resource_type = self._resource_type(qrmi)
		self._ensure_resource_env(type_name, alias, credential=credential)
		try:
			resource_obj = qrmi.QuantumResource(alias, resource_type)
		except Exception as exc:
			raise self._qrmi_error(
				exc,
				f"failed to open QRMI {type_name} resource "
				f"{alias!r}") from exc
		self._resource_objs[cache_key] = resource_obj
		logging.debug(
			"shim: QRMI resource opened (%s, %s)", type_name, alias)
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
				raise self._qrmi_error(
					exc, "failed to read QRMI target()") from exc
		return self._target_cache[cache_key]

	def _static_arch(self, target):
		# QRMI 0.22.0 added the static architecture to target(). Before that the
		# key was simply absent, which is why every caller here treats it as
		# optional. It arrives as a LIST -- one architecture per DUT -- while
		# qhw-iqm's normalizers want the single architecture dict and call
		# .get() on whatever they are handed, so passing the list straight
		# through raises AttributeError. An IQM server exposes one architecture,
		# so take the first dict and ignore anything past it. A bare dict is
		# tolerated too, in case the shape settles that way upstream.
		static = target.get("static_quantum_architecture")
		if isinstance(static, dict):
			return static
		for entry in (static or []):
			if isinstance(entry, dict):
				return entry
		return {}

	def _arch_raw(self):
		# Map target() to the {static_architecture, dynamic_architecture} shape
		# qhw-iqm's device/coupling normalizers expect.
		target = self._target()
		raw = {"dynamic_architecture":
				target.get("dynamic_quantum_architecture") or {}}
		static = self._static_arch(target)
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
		# provider fields plus an embedded qhw device record. The static
		# architecture is empty against QRMI older than 0.22.0, which did not
		# report it at all.
		from qhw_iqm import normalize_device
		target = self._target()
		dynamic = target.get("dynamic_quantum_architecture") or {}
		return {
			"backend": self._descriptor.get("provider", "iqm"),
			"metadata_supported": True,
			"static_architecture": self._static_arch(target),
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
			raise self._qrmi_error(exc, "QRMI task_start failed") from exc
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
			raise self._qrmi_error(exc, "QRMI task_result failed") from exc
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
			from iqm.station_control.interface.models import RunRequest
		except Exception as exc:
			raise DEFwExecutionError(
				"iqm-client is required to build the IQM job payload for QRMI "
				f"execution: {exc}") from exc
		calset = calibration_set_id
		if calset is not None and not isinstance(calset, str):
			calset = str(calset)
		try:
			# iqm-client >= 34 dropped the private _build_run_request helper.
			# RunRequest (a TypeAlias for PostJobsRequest) is built directly:
			# every compilation option the helper set now has a model default.
			# qubit_mapping stays None because build_iqm_circuit already emits
			# physical qubit names (see logical_to_physical_qubits).
			run_request = RunRequest(
				circuits=[iqm_circuit],
				calibration_set_id=calset,
				shots=int(shots))
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
				raise self._qrmi_error(
					exc, "QRMI task_status failed") from exc
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

	def get_task_timing(self, cid=None):
		job = self._last_job_for(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"timing": job.get("timing") or {}}

	def get_task_metadata(self, cid=None):
		job = self._last_job_for(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"shots": job.get("shots"),
			"backend": self._descriptor.get("provider", "iqm")}
