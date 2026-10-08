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
from util import instrumentation
import json
import logging
import os
import sys
import threading
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

# The object storage IBMQuantumSystem stages results through, as
# (QRMI variable suffix, QFW_IBM_* override, config key). The first four
# describe the store and come from device-access config. The last two are
# secret and come from the credential DB, so they are kept apart. QRMI reads
# S3_ENDPOINT_FOR_QSAPI as well as S3_ENDPOINT: a deployment can put the QS
# API on a different address from the one the client uses.
OBJECT_STORAGE_FIELDS = (
	("S3_ENDPOINT", "QFW_IBM_S3_ENDPOINT", "s3_endpoint"),
	("S3_ENDPOINT_FOR_QSAPI", "QFW_IBM_S3_ENDPOINT_FOR_QSAPI",
		"s3_endpoint_for_qsapi"),
	("S3_BUCKET", "QFW_IBM_S3_BUCKET", "s3_bucket"),
	("S3_REGION", "QFW_IBM_S3_REGION", "s3_region"),
)
OBJECT_STORAGE_SECRETS = (
	("AWS_ACCESS_KEY_ID", "QFW_IBM_AWS_ACCESS_KEY_ID", "aws_access_key_id"),
	("AWS_SECRET_ACCESS_KEY", "QFW_IBM_AWS_SECRET_ACCESS_KEY",
		"aws_secret_access_key"),
)
OBJECT_STORAGE_SECRET_KEYS = tuple(
	key for _suffix, _env, key in OBJECT_STORAGE_SECRETS)

# What IBMQuantumSystem cannot open without. QRMI resolves these with
# required_env(), so a missing one fails construction, and task_start fails
# later anyway because the client has no S3 config to stage results through.
# S3_ENDPOINT_FOR_QSAPI is the one that is genuinely optional.
OBJECT_STORAGE_REQUIRED = (
	"S3_ENDPOINT", "S3_BUCKET", "S3_REGION",
	"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
)

# IBMQuantumSystem also requires a job timeout, and it sits outside the
# per-service prefix as {resource}_QRMI_JOB_TIMEOUT_SECONDS. QRS and QCS read
# the same variable but treat it as optional, so only QS needs it supplied.
# It is a number rather than a credential, so a device can set
# job-timeout-seconds and otherwise it defaults, which is friendlier than
# failing for the want of a value we can pick.
JOB_TIMEOUT_SETTING = "QRMI_JOB_TIMEOUT_SECONDS"
JOB_TIMEOUT_ENV = "QFW_IBM_JOB_TIMEOUT_SECONDS"
JOB_TIMEOUT_KEY = "job_timeout_seconds"
DEFAULT_JOB_TIMEOUT_SECONDS = 300

# Setting the environment and constructing the resource are one step, for a
# qrmi that has no from_config().
#
# Such a qrmi takes its endpoint, key, CRN and object storage from the process
# environment at construction, so _ensure_resource_env writes those variables
# and QuantumResource() reads them. The shim QRC runs a circuit per thread,
# and circuits from different reservations resolve different credentials, so
# two threads interleaving those halves would have one of them open a resource
# from the other one's environment. That is another user's API key, silently,
# with the right resource id.
#
# The lock is module level rather than per driver because the environment is
# process wide and one shim process holds a driver per wired library.
#
# QuantumResource.from_config() (qrmi 0.25.0 and later) takes the settings as
# an argument instead, so _qpu prefers it and neither writes nor reads the
# process environment while constructing. Nothing is shared between threads on
# that path, so it does not take this lock. The lock stays for a qrmi that
# predates from_config(), and can go once the floor is 0.25.0.
RESOURCE_ENV_LOCK = threading.Lock()

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


DEFAULT_SHOTS = 1024


def num_shots(info: dict, default: int = DEFAULT_SHOTS) -> int:
	"""Return the shot count from a circuit info dict.

	Accepts both the canonical ``num_shots`` key and the legacy ``shots``
	alias, falling back to *default* when neither is present.
	"""
	return int(info.get("num_shots") or info.get("shots") or default)


def _first_error_line(logs):
	# Scan provider log text for the first line that looks like an error.
	# Returns that line (stripped) so the caller can append it to the
	# DEFwExecutionError message. Returns None when logs is empty/None or
	# when no error line is found.
	if not logs:
		return None
	for line in logs.splitlines():
		stripped = line.strip()
		if not stripped:
			continue
		lower = stripped.lower()
		if any(tok in lower for tok in ("error", "exception", "traceback", "failed", "fatal")):
			return stripped
	return None


class QrmiDriver(BaseDriver):
	name = "qrmi"
	CAPABILITIES = frozenset({
		"get_device_info",
		"get_coupling_graph",
		"get_calibration_snapshot",
		"get_dynamic_backend_info",
		"get_backend_info",
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
		self._resource_locks = {}
		self._resource_locks_guard = threading.Lock()
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
		# The IBM service CRN is returned too, from the credential or the user's
		# credential DB entry, when either names one. See _ensure_ibm_env.
		credential = dict(credential or {})
		provider = self._descriptor.get("provider", "iqm")
		service_crn = credential.get("service_crn")
		object_storage = {
			key: credential[key] for key in OBJECT_STORAGE_SECRET_KEYS
			if credential.get(key)}
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
					device_id=(credential.get("device_id")
						or self._descriptor.get("id")),
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
			service_crn = service_crn or cfg.get("service_crn")
			for key in OBJECT_STORAGE_SECRET_KEYS:
				if not object_storage.get(key) and cfg.get(key):
					object_storage[key] = cfg[key]
			provider_device_id = (
				provider_device_id
				or cfg.get("provider_device_id")
				or cfg.get("quantum_computer"))
		# Strip trailing slashes: QRMI's IQM client builds URLs as
		# f"{endpoint}/api/v1/...", so a configured base URL ending in "/"
		# yields "//api/v1/..." which the IQM server rejects (empty target).
		if base_url:
			base_url = base_url.rstrip("/")
		resolved = {
			"base_url": base_url,
			"token": token,
			"provider_device_id": provider_device_id,
			"quantum_computer": provider_device_id,
			"service_crn": service_crn,
		}
		resolved.update(object_storage)
		return resolved

	def _iqm_isa_settings(self, backend, credential=None):
		# What QRMI's IQM resource needs, as the unprefixed setting names both
		# sinks below work in: QRMI_IQM_ISA_ENDPOINT and QRMI_IQM_ISA_TOKEN. It
		# reads them when the resource is constructed. Inside a SLURM
		# reservation the SPANK plugin supplies them; outside one (e.g. a bare
		# introspection call) they are unset and the resource cannot be opened,
		# so resolve them from device-access config instead.
		#
		# A value already in the environment wins, so nothing here overrides
		# what the SPANK plugin set. A credential replaces what it supplies and
		# leaves the rest, which is why an unsupplied half falls back to the
		# environment rather than being cleared.
		endpoint_key = "QRMI_IQM_ISA_ENDPOINT"
		token_key = "QRMI_IQM_ISA_TOKEN"
		endpoint = os.environ.get(f"{backend}_{endpoint_key}")
		token = os.environ.get(f"{backend}_{token_key}")
		if credential:
			access = self._access(credential=credential)
			return {
				endpoint_key: access.get("base_url") or endpoint,
				token_key: access.get("token") or token,
			}, ()
		if endpoint and token:
			return {endpoint_key: endpoint, token_key: token}, ()
		access = self._access()
		return {
			endpoint_key: endpoint or access.get("base_url"),
			token_key: token or access.get("token"),
		}, (endpoint_key, token_key)

	def _ensure_iqm_isa_env(self, alias, credential=None):
		# Write the IQM pair into the process environment, for a qrmi with no
		# from_config(). QRMI keys the variables by the resource id up to the
		# first comma (backend_name,calibration_set_id), so match that prefix.
		backend = alias.split(",")[0]
		settings, required = self._iqm_isa_settings(
			backend, credential=credential)
		self._write_settings_env(backend, settings)
		missing = [key for key in required
				if not os.environ.get(f"{backend}_{key}")]
		if missing:
			raise self._missing_settings_error(backend, missing)

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

	def _write_settings_env(self, backend, settings):
		# Settings are named without the resource prefix, because that is the
		# form a config map takes. The environment wants them prefixed.
		# A None clears the variable rather than leaving it, for the reason
		# the IBM resolver returns one: a value this reservation did not
		# supply must not be served from the previous reservation's.
		for key, value in settings.items():
			name = f"{backend}_{key}"
			if value is None:
				os.environ.pop(name, None)
			else:
				os.environ[name] = str(value)

	def _missing_settings_error(self, backend, missing, kind=None):
		# One message for both sinks. It names the prefixed environment
		# variables, which stay actionable on the config path too because
		# _resource_config takes the environment as its base.
		names = [f"{backend}_{key}" for key in missing]
		if kind is None:
			return DEFwExecutionError(
				"QRMI IQM introspection needs " + " and ".join(names) +
				"; set them, or set QFW_QC_URL/QFW_API_KEY, or configure "
				"device access (these are normally injected by the SPANK "
				"plugin inside a reservation)")
		message = (
			"QRMI IBM access needs " + " and ".join(names) + ". The "
			"endpoint and API key come from device-access config or "
			"QFW_QC_URL/QFW_API_KEY. The service CRN comes from the "
			"user's service_crn entry in the credential DB, the device's "
			"service-crn key in device-access config, or "
			"QFW_IBM_SERVICE_CRN")
		if kind == "QS":
			message += (
				". IBMQuantumSystem also requires its object storage: the "
				"store from the device's s3-endpoint, s3-bucket and "
				"s3-region keys, and the key pair from the user's "
				"aws_access_key_id and aws_secret_access_key entries in "
				"the credential DB")
		return DEFwExecutionError(message)

	def _ibm_settings(self, kind, backend, credential=None):
		# What QRMI's IBM resources need, as unprefixed setting names. They
		# read endpoint, IAM endpoint, API key and service CRN when the
		# resource is constructed, and QRMI names each service's settings
		# after the service, so the kind selects the family.
		#
		# A credential bound to the caller's reservation always supplies the
		# endpoint and API key, replacing whatever is already set, as the IQM
		# resolver does. A value it does not supply resolves to None, which
		# clears rather than inherits: these settings outlive one call in a
		# long-running service, so filling in only what is missing would let
		# the first reservation's key open every later reservation's resource.
		# Without a credential only missing values are resolved and anything
		# already set is kept, since an operator or a SPANK plugin may have
		# set it.
		#
		# The endpoint and API key come from device-access config, the same
		# source the IQM path uses. The IAM endpoint belongs to the device, so
		# it comes from the device's iam-endpoint key through the descriptor,
		# and QFW_IBM_IAM_ENDPOINT still wins when set.
		#
		# The service CRN names the IBM instance a job runs under. An instance
		# can serve several devices and many users, and a user can be assigned
		# to several instances. So the CRN comes from, in order: the
		# reservation's credential, QFW_IBM_SERVICE_CRN, the user's credential
		# DB entry, and the device's service-crn key as the default. That is
		# the order _access uses for the endpoint and key. A site service
		# relies on the config sources, since nothing a user or a job exports
		# reaches it.
		prefix = f"QRMI_IBM_{kind}"
		endpoint_key = f"{prefix}_ENDPOINT"
		iam_endpoint_key = f"{prefix}_IAM_ENDPOINT"
		apikey_key = f"{prefix}_IAM_APIKEY"
		crn_key = f"{prefix}_SERVICE_CRN"

		def current(key):
			return os.environ.get(f"{backend}_{key}")

		settings = {}
		access = {}
		if credential:
			# No fallback on this path. A credential that cannot be resolved
			# has to fail rather than leave another reservation's endpoint or
			# key in place.
			access = self._access(credential=credential)
			settings[endpoint_key] = access.get("base_url")
			settings[apikey_key] = access.get("token")
		else:
			endpoint = current(endpoint_key)
			apikey = current(apikey_key)
			if not (endpoint and apikey):
				try:
					access = self._access(credential=credential)
				except DEFwExecutionError:
					# Fall through to the missing-setting report, which names
					# what to set. It is more actionable than a failure to
					# resolve device access the caller may not be relying on.
					access = {}
			settings[endpoint_key] = endpoint or access.get("base_url")
			settings[apikey_key] = apikey or access.get("token")

		settings[iam_endpoint_key] = current(iam_endpoint_key) or str(
			os.environ.get("QFW_IBM_IAM_ENDPOINT")
			or self._descriptor.get("iam_endpoint")
			or self._descriptor.get("iam-endpoint")
			or IBM_DEFAULT_IAM_ENDPOINT)

		crn = (dict(credential or {}).get("service_crn")
			or os.environ.get("QFW_IBM_SERVICE_CRN")
			or access.get("service_crn")
			or self._descriptor.get("service_crn")
			or self._descriptor.get("service-crn"))
		if credential:
			# Resolved for every reservation and cleared when nothing supplies
			# one, like the endpoint and key. A CRN can belong to the user, so
			# keeping the value already set would run this reservation under
			# the previous user's instance.
			settings[crn_key] = str(crn) if crn else None
		else:
			settings[crn_key] = current(crn_key) or (
				str(crn) if crn else None)

		# Object storage applies only to IBMQuantumSystem, which stages
		# results through a bucket. The other IBM services never read these.
		required = [endpoint_key, iam_endpoint_key, apikey_key, crn_key]
		if kind == "QS":
			settings.update(self._object_storage_settings(
				prefix, backend, access, credential))
			settings.update(self._job_timeout_settings(backend))
			required += [
				f"{prefix}_{suffix}" for suffix in OBJECT_STORAGE_REQUIRED]
			required.append(JOB_TIMEOUT_SETTING)
		return settings, required

	def _ensure_ibm_env(self, kind, alias, credential=None):
		# Write an IBM family into the process environment, for a qrmi with no
		# from_config().
		backend = alias.split(",")[0]
		settings, required = self._ibm_settings(
			kind, backend, credential=credential)
		self._write_settings_env(backend, settings)
		missing = [key for key in required
				if not os.environ.get(f"{backend}_{key}")]
		if missing:
			raise self._missing_settings_error(backend, missing, kind=kind)

	def _object_storage_settings(self, prefix, backend, access,
			credential=None):
		# QRMI's IBM Quantum System stages results through object storage and
		# reads six more settings for it.
		#
		# The bucket, region and the two endpoints describe the store, so they
		# come from the device's own device-access entry, the way iam-endpoint
		# does. The AWS key pair is secret, so it comes from the reservation's
		# credential or from the user's credential DB entry, never from the
		# admin-owned YAML. QFW_IBM_* wins for both, for an operator driving
		# the shim by hand.
		#
		# Config is what makes this reachable at all. Nothing a user or a job
		# exports reaches a site service, so an IBM Quantum System configured
		# only through the environment cannot be driven from one.
		settings = {}
		for suffix, env_name, key in OBJECT_STORAGE_FIELDS:
			setting_key = f"{prefix}_{suffix}"
			value = (
				os.environ.get(env_name)
				or self._descriptor.get(key)
				or self._descriptor.get(key.replace("_", "-")))
			settings[setting_key] = (
				os.environ.get(f"{backend}_{setting_key}")
				or (str(value) if value else None))

		credential = dict(credential or {})
		for suffix, env_name, key in OBJECT_STORAGE_SECRETS:
			setting_key = f"{prefix}_{suffix}"
			value = (
				credential.get(key)
				or os.environ.get(env_name)
				or access.get(key))
			if not credential:
				settings[setting_key] = (
					os.environ.get(f"{backend}_{setting_key}")
					or (str(value) if value else None))
				continue
			# With a credential these are replaced, not filled in, for the
			# reason the endpoint and key are: one reservation's key would
			# otherwise serve the next.
			settings[setting_key] = str(value) if value else None
		return settings

	def _job_timeout_settings(self, backend):
		# QRMI_JOB_TIMEOUT_SECONDS, which IBMQuantumSystem requires. Note it
		# is not under the per-service prefix. The value matches run_circuit's
		# own default, so QRMI does not abandon a job while this driver is
		# still polling for it. It is a number rather than a credential, so a
		# device can set job-timeout-seconds and otherwise it defaults, which
		# is friendlier than failing for the want of a value we can pick.
		current = os.environ.get(f"{backend}_{JOB_TIMEOUT_SETTING}")
		if current:
			return {JOB_TIMEOUT_SETTING: current}
		value = (
			os.environ.get(JOB_TIMEOUT_ENV)
			or self._descriptor.get(JOB_TIMEOUT_KEY)
			or self._descriptor.get(JOB_TIMEOUT_KEY.replace("_", "-"))
			or DEFAULT_JOB_TIMEOUT_SECONDS)
		return {JOB_TIMEOUT_SETTING: str(value)}

	def _resource_config(self, type_name, alias, credential=None):
		# The config map QuantumResource.from_config() opens a resource with,
		# so the resource gets its settings without the process environment
		# being involved at all. None means this driver has nothing to resolve
		# for the type and the caller should use the environment path.
		#
		# THE MAP STARTS FROM THE ENVIRONMENT rather than replacing it. QRMI
		# ignores the environment entirely once a map is given, and it reads
		# settings this driver does not model: QRMI_JOB_ACQUISITION_TOKEN, and
		# the QRS/QCS session settings SESSION_MODE, SESSION_ID,
		# SESSION_MAX_TTL and TIMEOUT_SECONDS. The SPANK plugin sets the
		# acquisition token and SESSION_MODE, so a map built only from what is
		# resolved here would silently drop them and change how a job runs.
		# Taking every {backend}_QRMI_* variable as the base keeps those
		# working, and keeps "what the operator or SPANK set wins" true for
		# the settings that are filled in rather than replaced.
		backend = alias.split(",")[0]
		kind = None
		if type_name == "IQMServer":
			settings, required = self._iqm_isa_settings(
				backend, credential=credential)
		else:
			kind = IBM_RESOURCE_ENV_KINDS.get(type_name)
			if not kind:
				return None
			settings, required = self._ibm_settings(
				kind, backend, credential=credential)

		prefix = f"{backend}_"
		config = {
			name[len(prefix):]: value
			for name, value in os.environ.items()
			if name.startswith(f"{prefix}QRMI_")
		}
		for key, value in settings.items():
			if value is None:
				config.pop(key, None)
			else:
				config[key] = str(value)

		missing = [key for key in required if not config.get(key)]
		if missing:
			raise self._missing_settings_error(backend, missing, kind=kind)
		return config

	def _resource_lock(self, cache_key):
		# One construction lock per credential. Two reservations have nothing
		# to serialize once the process environment is out of the picture, but
		# two threads sharing a credential must still open one resource
		# between them: a discarded duplicate would leak the tokio runtime
		# QRMI keeps in a ManuallyDrop.
		with self._resource_locks_guard:
			return self._resource_locks.setdefault(
				cache_key, threading.Lock())

	def _open_resource(self, open_resource, type_name, alias):
		try:
			return open_resource()
		except Exception as exc:
			raise self._qrmi_error(
				exc,
				f"failed to open QRMI {type_name} resource "
				f"{alias!r}") from exc

	def _qpu(self, credential=None):
		# Lazy: open the QRMI QuantumResource this descriptor names. target()
		# is not reservation-bound, so introspection works without acquire() as
		# long as the resource's settings can be resolved, which the resolvers
		# above do from device-access config when no reservation has supplied
		# them.
		#
		# Two ways in. A qrmi with from_config() (0.25.0 and later) takes the
		# settings as an argument, so the resource is built from a map and the
		# process environment is never written. Otherwise the settings have to
		# be written to the environment for the constructor to read back,
		# which is why that path holds RESOURCE_ENV_LOCK across both halves.
		cache_key = self._credential_cache_key(credential)
		cached = self._resource_objs.get(cache_key)
		if cached is not None:
			return cached
		qrmi = self._resource()
		alias = self._qc_alias(credential=credential)
		if not alias:
			raise DEFwExecutionError(
				"QRMI introspection needs a QFw device id; set "
				"QFW_QPU_DEVICE_ID or configure a device descriptor")
		type_name, resource_type = self._resource_type(qrmi)

		config = None
		if hasattr(qrmi.QuantumResource, "from_config"):
			# None means this driver resolves nothing for the type (Pasqal,
			# Alice & Bob), so the environment is still the only source and
			# the fallback below is the right path for it.
			config = self._resource_config(
				type_name, alias, credential=credential)

		if config is not None:
			with self._resource_lock(cache_key):
				# Another thread may have opened this very resource while this
				# one waited, so look again before building a second.
				cached = self._resource_objs.get(cache_key)
				if cached is not None:
					return cached
				resource_obj = self._open_resource(
					lambda: qrmi.QuantumResource.from_config(
						alias, resource_type, config),
					type_name, alias)
				self._resource_objs[cache_key] = resource_obj
		else:
			with RESOURCE_ENV_LOCK:
				cached = self._resource_objs.get(cache_key)
				if cached is not None:
					return cached
				self._ensure_resource_env(
					type_name, alias, credential=credential)
				resource_obj = self._open_resource(
					lambda: qrmi.QuantumResource(alias, resource_type),
					type_name, alias)
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
			credential.get("service_crn"),
			credential.get("aws_access_key_id"),
		)

	def _target(self, credential=None):
		# QRMI target() is a remote call returning raw IQM JSON (dynamic
		# architecture / calibration_set / quality_metrics). Parse it once per
		# driver instance and serve every introspection call from the cache, so
		# the four calls don't each re-fetch the same payload.
		cache_key = self._credential_cache_key(credential)
		if cache_key not in self._target_cache:
			try:
				target_data = json.loads(
					self._qpu(credential=credential).target().value)
			except Exception as exc:
				raise self._qrmi_error(
					exc, "failed to read QRMI target()") from exc

			# IBM target() returns null for configuration or properties on failure.
			# To avoid caching a failed or half-failed payload (which would stick
			# in the cache after IBM recovers), do not cache if either is None.
			if self._provider() == "ibm":
				if (
					isinstance(target_data, dict)
					and target_data.get("configuration") is not None
					and target_data.get("properties") is not None
				):
					self._target_cache[cache_key] = target_data
			else:
				self._target_cache[cache_key] = target_data
			return target_data
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

	def _provider(self):
		return str(self._descriptor.get("provider") or "iqm").lower()

	# --- introspection facet: reuse qhw-iqm / qhw-ibm on QRMI's raw data -

	def get_device_info(self):
		if self._provider() == "ibm":
			from qhw_ibm import normalize_device
			target_config_json = self._target().get("configuration") or {}
			if not target_config_json:
				raise DEFwExecutionError("QRMI failed to retrieve the backend data")
			return normalize_device(target_config_json, device_id=self._device_id())
		from qhw_iqm import normalize_device
		return normalize_device(self._arch_raw(), device_id=self._device_id())

	def get_coupling_graph(self, calibration_set_id=None):
		if self._provider() == "ibm":
			from qhw_ibm import normalize_coupling
			target_config_json = self._target().get("configuration") or {}
			if not target_config_json:
				raise DEFwExecutionError("QRMI failed to retrieve the backend data")
			return normalize_coupling(target_config_json, device_id=self._device_id())
		from qhw_iqm import normalize_coupling
		return normalize_coupling(self._arch_raw(), device_id=self._device_id())

	def get_calibration_snapshot(self, calibration_set_id=None):
		if self._provider() == "ibm":
			# The calibration normalizer reads the properties sub-dict
			# (qubits, gates, last_update_date).
			from qhw_ibm import normalize_calibration
			target_props_json = self._target().get("properties") or {}
			if not target_props_json:
				raise DEFwExecutionError("QRMI failed to retrieve the backend data")
			return normalize_calibration(target_props_json, device_id=self._device_id())
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
		if self._provider() == "ibm":
			# IBM target() has no live dynamic state; configuration and
			# properties are static snapshots with no calibration_set_id or
			# active qubit list equivalent.
			return {
				"backend": "ibm",
				"metadata_supported": False,
				"dynamic_architecture": {},
			}
		# The dynamic architecture as-is, matching the native svc_iqm_qpm shape
		# (a provider dict, not a qhw record).
		return {
			"backend": self._descriptor.get("provider", "iqm"),
			"metadata_supported": True,
			"dynamic_architecture":
				self._target().get("dynamic_quantum_architecture") or {},
		}

	def get_backend_info(self):
		if self._provider() == "ibm":
			from qhw_ibm import normalize_device
			target_config_json = self._target().get("configuration") or {}
			if not target_config_json:
				raise DEFwExecutionError("QRMI failed to retrieve the backend data")
			qhw_device = normalize_device(target_config_json, device_id=self._device_id())
			qubits_len = len(qhw_device.get("qubits", []))
			n_qubits = target_config_json.get("n_qubits", qubits_len)
			return {
				"backend": "ibm",
				"metadata_supported": True,
				"static_architecture": target_config_json,
				"active_qubits": list(range(n_qubits)),
				"calibration_set_id": None,
				"qhw_device": qhw_device,
			}

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

	# --- execution: OpenQASM/QPY -> QRMI task lifecycle ----------------------

	def run_circuit(self, source):
		# The circuit arrives in a format this QPM declares, QPY or OpenQASM 2
		# (see util.circuit_payload). Transcode it to an IQM circuit with the
		# shared util, submit through QRMI's task lifecycle, poll to completion,
		# and normalize the counts to qhw-result-v1 (the same normalizer the
		# native svc_iqm_qpm path uses). QRMI-for-IQM has no acquire/release, so
		# there is no reservation step.
		provider = self._provider()
		if provider == "ibm":
			return self._run_ibm_circuit(source)
		return self._run_iqm_circuit(source)

	def _run_ibm_circuit(self, source):
		# Lifecycle: build backend target -> build Qiskit SamplerV2 payload ->
		# submit via QRMI -> normalize the result to qhw-result-v1.
		from util.circuit_payload import is_qpy_circuit_provided, QPY_FORMATS
		info = getattr(source, "info", None) or {}
		if not is_qpy_circuit_provided(info):
			raise DEFwExecutionError(
				"A circuit targeted to run in an IBM Quantum device must be serialized"
				" using QPY to preserve all its properties. Please update the data and"
				" format fields in the info[circuit] provided accordingly to a"
				f" QPY format: {QPY_FORMATS}")

		with instrumentation.backend_phase("acquire"):
			target, target_config_json = self._build_backend_target(source)
		with instrumentation.qpm_transpile():
			payload = self._build_ibm_payload(source, target)
		result_json, job = self._run_sampler_payload(payload, source)

		# Update last_job with IBM measurements.
		if job is not None:
			results = result_json.get("results") or []
			data = results[0].get("data") or {}
			job["measurements"] = data
			self._last_job = job

		# The JSON returned by qrmi.task_start is a Qiskit runtime SamplerV2, which is
		# focused on the measured samples and does not include device and job information.
		# They are both returned by separated API calls.
		raw = {
			"device": {
				"raw_configuration": target_config_json,
			},
			"job": job,
			"raw_results": result_json
		}

		from qhw_ibm import normalize_result
		return normalize_result(raw, device_id=self._device_id())

	def _task_logs(self, job_id, credential=None):
		# Best effort: the caller is already failing, so anything that goes
		# wrong here is logged and dropped rather than raised. A backend that
		# does not implement task_logs raises UnsupportedFunctionError.
		try:
			return str(self._qpu(credential=credential).task_logs(job_id))
		except Exception as exc:
			logging.warning(
				"shim: QRMI task_logs for job %s failed: %s", job_id, exc)
			return ""

	def _run_sampler_payload(self, payload, source):
		# Submit the SamplerV2 payload via QRMI task_start -> poll to
		# completion -> fetch task_result JSON. Time is measured along the way.
		info = getattr(source, "info", None) or {}
		credential = getattr(source, "provider_credential", None)
		cid = source.get_cid() if hasattr(source, "get_cid") else info.get("cid")

		shots = num_shots(info)
		timeout = float(info.get("timeout", DEFAULT_JOB_TIMEOUT_SECONDS))
		poll = float(info.get("poll_interval", 1.0))

		# Set by the shim QRC when the QPM cancels this circuit. A cancel that
		# arrives before submission starts nothing at the provider.
		cancel_event = getattr(source, "cancel_event", None)
		if cancel_event is not None and cancel_event.is_set():
			raise DEFwExecutionError(
				"QRMI job was cancelled before it was submitted")

		timing = {}
		start = time.monotonic()
		with instrumentation.backend_phase("acquire"):
			qpu = self._qpu(credential=credential)
		with instrumentation.backend_phase("submit"):
			try:
				job_id = qpu.task_start(payload)
			except Exception as exc:
				raise self._qrmi_error(exc, "QRMI task_start failed") from exc
		timing["submit_seconds"] = time.monotonic() - start
		instrumentation.set_attribute(
			instrumentation.ATTR_VENDOR_JOB_ID, str(job_id))

		collect = instrumentation.backend_phase("collect")
		collect.__enter__()
		instrumentation.set_attribute(
			instrumentation.ATTR_POLL_INTERVAL, float(poll))
		try:
			status = self._poll_task(
				job_id, timeout, poll, credential=credential,
				cancel_event=cancel_event)
		except BaseException:
			collect.__exit__(*sys.exc_info())
			raise
		timing["wait_seconds"] = (
			time.monotonic() - start - timing["submit_seconds"])
		if status != "completed":
			collect.__exit__(None, None, None)
			# task_status reports the state, not the cause. The provider's own log
			# is the only place the reason exists, so it goes to the operator in
			# full and to the caller as one line.
			logs = self._task_logs(job_id, credential=credential)
			# Nothing else to do with the failed job, we can delete it.
			self._stop_task(job_id, credential=credential)
			if logs:
				logging.error(
					"shim: QRMI job %s %s; provider logs:\n%s",
					job_id, status, logs)
			job = {
				"id": str(job_id), "status": status, "cid": cid,
				"timing": timing, "shots": shots, "logs": logs}
			self._last_job = job
			message = f"QRMI job {job_id} finished with status {status!r}"
			reason = _first_error_line(logs)
			if reason:
				message += f": {reason}"
			raise DEFwExecutionError(message)

		result_started = time.monotonic()
		try:
			result_json = json.loads(
				self._qpu(credential=credential).task_result(job_id).value)
		except Exception as exc:
			collect.__exit__(*sys.exc_info())
			raise self._qrmi_error(exc, "QRMI task_result failed") from exc
		collect.__exit__(None, None, None)
		timing["result_fetch_seconds"] = time.monotonic() - result_started
		timing["total_wall_seconds"] = time.monotonic() - start

		# Nothing else to do with the completed task, we can delete it.
		# The same behavior is implemented in QRMI's task_runner.
		self._stop_task(job_id, credential=credential)

		job = {"id": str(job_id), "status": "completed", "cid": cid,
			   "timing": timing, "shots": shots}
		self._last_job = job

		return result_json, job

	def _build_backend_target(self, source):
		# Fetch the raw backend configuration and properties via QRMI, then
		# convert them to a Qiskit Target used for ISA compilation.
		try:
			from qiskit_ibm_runtime.utils.backend_converter import convert_to_target
			from qiskit_ibm_runtime.models import BackendProperties, BackendConfiguration
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to import qiskit_ibm_runtime models: {exc}") from exc

		target_data = self._target(credential=getattr(source, "provider_credential", None))
		target_config_json = target_data.get("configuration")
		target_props_json = target_data.get("properties")

		if not target_config_json or not target_props_json:
			raise DEFwExecutionError("QRMI failed to retrieve the backend data")

		backend_config = BackendConfiguration.from_dict(target_config_json)
		backend_props = BackendProperties.from_dict(target_props_json)
		target = convert_to_target(backend_config, backend_props)
		return target, target_config_json

	def _build_ibm_payload(self, source, target):
		# Transcode the circuit to an ISA circuit against the backend target,
		# then wrap it as a Qiskit SamplerV2 primitive payload for QRMI.
		info = getattr(source, "info", None) or {}
		shots = num_shots(info)
		compilation_options = info.get("compilation_options") or {}
		param_values = info.get("param_values") or {}

		from util.circuit_payload import qiskit_input
		qiskit_circuit = qiskit_input(info)

		try:
			from qiskit.circuit import QuantumCircuit
		except Exception as exc:
			raise DEFwExecutionError("Failed to import qiskit QuantumCircuit") from exc

		if (
			isinstance(qiskit_circuit, QuantumCircuit)
			and qiskit_circuit.num_parameters
			and not param_values
		):
			raise DEFwExecutionError(
				  "'param_values' must be defined in info (as a dict keyed by"
				  " parameter name strings) for the following circuit parameters:"
				  f" {[p.name for p in qiskit_circuit.parameters]}")

		from util.ibm_transcode import compile_circuit
		isa_circuit = compile_circuit(qiskit_circuit, target, compilation_options)

		from util.ibm_transcode import qiskit_sampler_input_json
		input_json = qiskit_sampler_input_json(isa_circuit, param_values, shots)

		qrmi = self._resource()
		return qrmi.Payload.QiskitPrimitive(input=input_json, program_id="sampler")

	def _run_iqm_circuit(self, source):
		# Build IQM run request -> wrap as Payload.IQMServer -> delegate
		# submit/poll/fetch to _run_sampler_payload -> normalize to
		# qhw-result-v1 and patch measurements into _last_job.
		qrmi = self._resource()

		info = getattr(source, "info", None) or {}
		shots = num_shots(info)
		mapping = info.get("iqm_qubit_mapping") or info.get("qubit_mapping")
		use_timeslot = bool(info.get("use_timeslot", False))

		with instrumentation.backend_phase("acquire"):
			target = self._target(
				credential=getattr(source, "provider_credential", None))
		dynamic = target.get("dynamic_quantum_architecture") or {}
		calibration_set_id = (
			info.get("calibration_set_id")
			or info.get("iqm_calibration_set_id")
			or dynamic.get("calibration_set_id"))

		from util.circuit_payload import qiskit_input
		from util.iqm_transcode import build_iqm_circuit
		with instrumentation.qpm_transpile():
			circuit = qiskit_input(info)
			iqm_circuit = build_iqm_circuit(circuit, dynamic, mapping)
			iqmjson, run_request = self._build_iqmjson(
					iqm_circuit, shots, calibration_set_id)

		payload = qrmi.Payload.IQMServer(
			iqmjson=iqmjson, job_type="circuit",
			use_timeslot=use_timeslot, tag=None)

		result_json, job = self._run_sampler_payload(payload, source)

		# Update last_job with IQM measurements
		if job is not None:
			job["measurements"] = result_json.get("measurements") or {}
			self._last_job = job

		measurement_counts = result_json.get("measurement_counts")
		circuits = run_request.get("circuits") if isinstance(
				run_request, dict) else None
		job_id = job.get("id") if job is not None else None
		raw = {
			"job": {"id": job_id, "status": "completed"},
			"run_request": run_request if isinstance(run_request, dict) else {},
			"measurement_counts": measurement_counts,
			"circuits": circuits or [],
		}
		from qhw_iqm import normalize_result
		return normalize_result(raw, device_id=self._device_id())

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

	def _poll_task(self, job_id, timeout, poll, credential=None,
			cancel_event=None):
		# Poll QRMI task_status until a terminal state, and return
		# completed, failed or cancelled.
		#
		# A cancel from the QPM (cancel_event, set by the shim QRC) or the
		# timeout stops the provider job with task_stop, so it does not keep
		# running after QFw has given up on it. The wait between polls ends as
		# soon as a cancel arrives.
		deadline = time.monotonic() + max(timeout, 0.0)
		polls = 0
		while True:
			if cancel_event is not None and cancel_event.is_set():
				self._stop_task(job_id, credential=credential)
				return "cancelled"
			try:
				raw = self._qpu(credential=credential).task_status(job_id)
			except Exception as exc:
				raise self._qrmi_error(
					exc, "QRMI task_status failed") from exc
			state = _status_str(raw)
			polls += 1
			instrumentation.add_event("poll", {"qfw.vendor.status": state})
			instrumentation.set_attribute(instrumentation.ATTR_POLL_COUNT, polls)
			if "complet" in state:
				return "completed"
			if "fail" in state or "error" in state:
				return "failed"
			if "cancel" in state:
				return "cancelled"
			if time.monotonic() >= deadline:
				stop_error = self._stop_task(job_id, credential=credential)
				stopped = (
					f"task_stop failed: {stop_error}" if stop_error
					else "it was stopped")
				raise DEFwExecutionError(
					f"QRMI job {job_id} timed out after {timeout}s "
					f"(last status {state!r}), and {stopped}")
			if cancel_event is not None:
				cancel_event.wait(max(poll, 0.0))
			else:
				time.sleep(max(poll, 0.0))

	def _stop_task(self, job_id, credential=None):
		# Best effort, since the job may already have ended at the provider.
		# Returns the error text, or None when task_stop succeeded.
		try:
			self._qpu(credential=credential).task_stop(job_id)
		except Exception as exc:
			logging.warning(
				"shim: QRMI task_stop for job %s failed: %s", job_id, exc)
			return str(exc)
		return None

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
