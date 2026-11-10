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
# the QDMI driver reads device info via MQT Core's QDMI API and normalizes it
# with qdmi_normalize.) target() is not reservation-bound, so introspection works
# without acquire().
#
# Milestone status (design doc qpu-frontend-contract.md section 13): the whole
# introspection facet is wired from one cached target() payload —
# get_device_info / get_coupling_graph (-> qhw-iqm device/coupling),
# get_calibration_snapshot (-> qhw-iqm calibration), get_dynamic_backend_info
# (the dynamic architecture, native shape), and get_backend_info (native
# composite with an embedded qhw device). Circuit execution uses QRMI's task
# lifecycle and returns normalized qhw results.

from .base_driver import (BaseDriver, JobRecords, Sessions, next_poll_wait,
		poll_schedule, reservation_cache_key)
from .qrmi_profiles import (DEFAULT_JOB_TIMEOUT_SECONDS, JOB_TIMEOUT_KEY,
		PASQAL_CONFIG_ROOT,
		PROFILES, num_shots,
		profile_for, profile_for_resource_type)
from defw_exception import (DEFwExecutionError, DEFwNotFound,
		DEFwNotReady)
from util import instrumentation
import json
import logging
import os
import random
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
	**{name: profile.resource_types for name, profile in PROFILES.items()},
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


# A provider that rate-limits the shim answers HTTP 429. QRMI 0.26 has no class
# for that, so a 429 arrives as its base QrmiError, with the status only in the
# message. reqwest spells it the same way for every vendor:
#
#   IQM              error in response: status code 429 Too Many Requests
#   IBM QRS and QCS  response body: 429 Too Many Requests ...
#   Pasqal Cloud     Status: 429 Too Many Requests, Fail ...
#
# IBMQuantumSystem retries a 429 inside QRMI, without a limit, so it never
# reaches here.
#
# None of them passes on the response's Retry-After, so the wait is the
# driver's own. It doubles from RATE_LIMIT_FIRST_WAIT up to RATE_LIMIT_MAX_WAIT,
# and each wait is drawn from the upper half of that, so circuits refused
# together do not all come back together. IQM blocks an account that runs over
# its request quota for 30 seconds, as QDMI-on-IQM 1.5.0 notes. A status poll
# or a result fetch can be refused then as well as a submission, so every call
# of a job's lifecycle waits a 429 out, and none of them gives up before the
# job's own timeout runs out (_RateLimit).
RATE_LIMITED = "429 too many requests"
RATE_LIMIT_FIRST_WAIT = 2.0
RATE_LIMIT_MAX_WAIT = 60.0


def _rate_limited(exc):
	return RATE_LIMITED in str(exc).lower()


def _rate_limit_wait(retry):
	# The wait before retry number `retry`, counting from zero.
	ceiling = min(RATE_LIMIT_MAX_WAIT,
		RATE_LIMIT_FIRST_WAIT * 2 ** min(retry, 16))
	return random.uniform(ceiling / 2, ceiling)


class _RateLimit:
	# Waits out a provider's rate limit for one QRMI call of one job. A wait
	# never runs past the job's deadline, and it ends as soon as the circuit
	# is cancelled.
	def __init__(self, call, deadline=None, cancel_event=None):
		self.call = call
		self.deadline = deadline
		self.cancel_event = cancel_event
		self.retries = 0

	def wait(self, at_least=0.0):
		# True once the wait is over and the call can be made again. False
		# when the deadline has passed, or a cancel arrived meanwhile.
		delay = max(at_least, _rate_limit_wait(self.retries))
		if self.deadline is not None:
			remaining = self.deadline - time.monotonic()
			if remaining <= 0:
				return False
			delay = min(delay, remaining)
		self.retries += 1
		logging.warning(
			"shim: QRMI %s was refused as rate-limited (HTTP 429); "
			"retry %d in %.1fs", self.call, self.retries, delay)
		instrumentation.add_event(
			"rate-limited", {"qfw.backend.wait_s": round(delay, 3)})
		if self.cancel_event is not None:
			return not self.cancel_event.wait(delay)
		time.sleep(delay)
		return True

	def cancelled(self):
		return self.cancel_event is not None and self.cancel_event.is_set()


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
		self._sessions = Sessions()
		self._resource_objs = self._sessions.opened
		self._resource_locks = self._sessions.locks
		self._target_cache = {}
		self._jobs = JobRecords("QRMI")
		self._profile = profile_for(self._descriptor)

	def implements(self, call):
		return self._profile.serves(call, self.CAPABILITIES)

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

	def _profile_for_type(self, type_name):
		return profile_for_resource_type(type_name, self._descriptor)

	def _ensure_ibm_env(self, kind, alias, credential=None):
		self._profile_for_type("IBMQuantumSystem")._ensure_ibm_env(
			self, kind, alias, credential=credential)

	def _ensure_iqm_isa_env(self, alias, credential=None):
		self._profile_for_type("IQMServer")._ensure_iqm_isa_env(
			self, alias, credential=credential)

	def _build_iqmjson(self, iqm_circuit, shots, calibration_set_id):
		return self._profile_for_type("IQMServer")._build_iqmjson(
			self, iqm_circuit, shots, calibration_set_id)

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
		return self._profile.access(credential)

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
		# the families we can resolve; PasqalLocal and Alice & Bob have no
		# device-access mapping yet and rely on the environment already
		# carrying theirs, from the SPANK plugin or the operator.
		profile = profile_for_resource_type(type_name, self._descriptor)
		if profile:
			profile.ensure_env(
				self, type_name, alias, credential=credential)

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
		if kind == "PASQAL":
			return DEFwExecutionError(
				"a Pasqal Cloud job needs " + " and ".join(names) +
				". The project comes from the user's project_id entry in "
				"the credential DB, the device's project-id key in "
				"device-access config, or the config file under "
				f"{PASQAL_CONFIG_ROOT}")
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
		profile = profile_for_resource_type(type_name, self._descriptor)
		resolved = profile and profile.resource_settings(
			self, type_name, backend, credential=credential)
		if resolved is None:
			return None
		settings, required, kind = resolved

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
			# None means this driver resolves nothing for the type (PasqalLocal,
			# Alice & Bob), so the environment is still the only source and
			# the fallback below is the right path for it.
			config = self._resource_config(
				type_name, alias, credential=credential)

		if config is not None:
			resource_obj = self._sessions.open(cache_key, lambda: self._open_resource(
				lambda: qrmi.QuantumResource.from_config(
					alias, resource_type, config),
				type_name, alias))
		else:
			def build():
				self._ensure_resource_env(
					type_name, alias, credential=credential)
				return self._open_resource(
					lambda: qrmi.QuantumResource(alias, resource_type),
					type_name, alias)

			with RESOURCE_ENV_LOCK:
				resource_obj = self._sessions.open(cache_key, build)
		logging.debug(
			"shim: QRMI resource opened (%s, %s)", type_name, alias)
		return resource_obj

	def evict_reservation(self, reservation_id):
		# The reservation has ended: drop its resource and target, so the
		# token it opened with goes too, and so do the resource's runtime
		# threads.
		cache_key = reservation_cache_key(reservation_id)
		resource = self._sessions.drop(cache_key)
		target = self._target_cache.pop(cache_key, None)
		if resource is not None:
			logging.debug(
				"shim: QRMI resource for reservation %s dropped",
				reservation_id)
		return resource is not None or target is not None

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

			if self._profile.is_complete_target(target_data):
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

	def _arch_raw(self, credential=None):
		# Map target() to the {static_architecture, dynamic_architecture} shape
		# qhw-iqm's device/coupling normalizers expect.
		target = self._target(credential=credential)
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
		return self._profile.device_info(self)

	def get_coupling_graph(self, calibration_set_id=None):
		return self._profile.coupling_graph(self)

	def get_calibration_snapshot(self, calibration_set_id=None):
		return self._profile.calibration_snapshot(self)

	def get_dynamic_backend_info(self, calibration_set_id=None):
		return self._profile.dynamic_backend_info(self)

	def get_backend_info(self, credential=None):
		return self._profile.backend_info(self, credential=credential)

	# --- execution: OpenQASM/QPY -> QRMI task lifecycle ----------------------

	def run_circuit(self, source):
		# The circuit arrives in a format this QPM declares, QPY or OpenQASM 2
		# (see util.circuit_payload). Transcode it to an IQM circuit with the
		# shared util, submit through QRMI's task lifecycle, poll to completion,
		# and normalize the counts to qhw-result-v1 (the same normalizer the
		# native svc_iqm_qpm path uses). QRMI-for-IQM has no acquire/release, so
		# there is no reservation step.
		return self._profile.run_circuit(self, source)

	def _vendor_timing(self, job_id, credential=None):
		# The provider's own account of the job for the telemetry, behind
		# QFW_TELEMETRY_VENDOR because it is one more request per job. Best
		# effort, like the logs.
		if not instrumentation.vendor_enabled():
			return {}
		phases = self._profile.vendor_timing(
			self, job_id, credential=credential)
		if phases:
			instrumentation.record_vendor_timing(phases)
		return phases

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

	def _job_call(self, call, make_call, timeout, deadline,
			cancel_event=None):
		# One QRMI call of a job's lifecycle, made again while the provider
		# refuses it as rate-limited, until the job's deadline. Any other
		# failure is raised at once, as before.
		rate_limit = _RateLimit(call, deadline, cancel_event)
		while True:
			try:
				return make_call()
			except Exception as exc:
				if not _rate_limited(exc):
					raise self._qrmi_error(
						exc, f"QRMI {call} failed") from exc
				if rate_limit.wait():
					continue
				if rate_limit.cancelled():
					raise DEFwExecutionError(
						f"QRMI job was cancelled while its {call} "
						"waited out a rate limit") from exc
				raise self._qrmi_error(
					exc, f"QRMI {call} was still rate-limited when the "
					f"{timeout}s job timeout ran out, after "
					f"{rate_limit.retries} retries") from exc

	def _run_sampler_payload(self, payload, source, delete_finished=True,
			default_timeout=DEFAULT_JOB_TIMEOUT_SECONDS):
		# Submit the SamplerV2 payload via QRMI task_start -> poll to
		# completion -> fetch task_result JSON. Time is measured along the way.
		# delete_finished says whether task_stop deletes a finished task. It
		# does not on Pasqal, where task_stop cancels the batch and Pasqal
		# rejects cancelling a batch that has already ended. default_timeout
		# is the provider deadline when neither the client nor the device
		# sets one, None for none.
		info = getattr(source, "info", None) or {}
		credential = getattr(source, "provider_credential", None)
		cid = source.get_cid() if hasattr(source, "get_cid") else info.get("cid")

		shots = num_shots(info)
		poll, backoff = poll_schedule(info, self._descriptor)

		# Set by the shim QRC when the QPM cancels this circuit. A cancel that
		# arrives before submission starts nothing at the provider.
		cancel_event = getattr(source, "cancel_event", None)
		if cancel_event is not None and cancel_event.is_set():
			raise DEFwExecutionError(
				"QRMI job was cancelled before it was submitted")

		# A device whose provider queues jobs sets the wait with
		# job-timeout-seconds, the key IBMQuantumSystem also hands QRMI as its
		# job timeout. A timeout the client sends still wins. Reaching it
		# stops the provider job (_poll_task).
		timeout = info.get("timeout",
			self._descriptor.get(JOB_TIMEOUT_KEY) or default_timeout)
		# Checked before anything is submitted: a value _poll_task cannot
		# read would otherwise fail with the job already at the provider.
		if timeout is not None:
			try:
				timeout = float(timeout)
			except (TypeError, ValueError):
				raise DEFwExecutionError(
					f"the job timeout must be a number of seconds, not "
					f"{timeout!r} (job-timeout-seconds or the client's "
					"timeout)") from None

		timing = {}
		start = time.monotonic()
		# One deadline for the whole wait, counted from here, so time spent
		# waiting out a rate limit (_RateLimit) comes off the job's timeout
		# rather than adding to it.
		deadline = (None if timeout is None
			else start + max(timeout, 0.0))
		with instrumentation.backend_phase("acquire"):
			qpu = self._qpu(credential=credential)
		with instrumentation.backend_phase("submit"):
			job_id = self._job_call(
				"task_start", lambda: qpu.task_start(payload),
				timeout, deadline, cancel_event)
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
				cancel_event=cancel_event, deadline=deadline,
				backoff=backoff)
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
			if delete_finished:
				self._stop_task(job_id, credential=credential)
			if logs:
				logging.error(
					"shim: QRMI job %s %s; provider logs:\n%s",
					job_id, status, logs)
			job = {
				"id": str(job_id), "status": status, "cid": cid,
				"timing": timing, "shots": shots, "logs": logs}
			self._jobs.put(job)
			message = f"QRMI job {job_id} finished with status {status!r}"
			reason = _first_error_line(logs)
			if reason:
				message += f": {reason}"
			raise DEFwExecutionError(message)

		result_started = time.monotonic()
		try:
			result_json = self._job_call(
				"task_result",
				lambda: json.loads(self._qpu(
					credential=credential).task_result(job_id).value),
				timeout, deadline, cancel_event)
		except BaseException:
			collect.__exit__(*sys.exc_info())
			raise
		collect.__exit__(None, None, None)
		timing["result_fetch_seconds"] = time.monotonic() - result_started
		timing["total_wall_seconds"] = time.monotonic() - start
		vendor = self._vendor_timing(job_id, credential=credential)

		# Nothing else to do with the completed task, we can delete it.
		# The same behavior is implemented in QRMI's task_runner.
		if delete_finished:
			self._stop_task(job_id, credential=credential)

		job = {"id": str(job_id), "status": "completed", "cid": cid,
			   "timing": timing, "shots": shots}
		if vendor:
			job["vendor_timing"] = vendor
		self._jobs.put(job)

		return result_json, job

	def _poll_task(self, job_id, timeout, poll, credential=None,
			cancel_event=None, deadline=None, backoff=False):
		# Poll QRMI task_status until a terminal state, and return
		# completed, failed or cancelled.
		#
		# A cancel from the QPM (cancel_event, set by the shim QRC) or the
		# timeout stops the provider job with task_stop, so it does not keep
		# running after QFw has given up on it. The wait between polls ends as
		# soon as a cancel arrives. A timeout of None sets no deadline.
		#
		# deadline is when the timeout runs out. _run_sampler_payload counts
		# it from the submission. Without one, it is counted from the first
		# poll. A poll the provider refuses as rate-limited is waited out and
		# made again, inside the same deadline.
		#
		# poll is the first wait between polls. With backoff, each poll that
		# finds the job unfinished doubles it (poll_schedule). No wait runs
		# past the deadline, so the last poll is made when it runs out.
		if deadline is None and timeout is not None:
			deadline = time.monotonic() + max(timeout, 0.0)
		rate_limit = _RateLimit("task_status", deadline, cancel_event)
		wait = poll
		polls = 0
		state = None
		while True:
			if cancel_event is not None and cancel_event.is_set():
				self._stop_task(job_id, credential=credential)
				return "cancelled"
			try:
				raw = self._qpu(credential=credential).task_status(job_id)
			except Exception as exc:
				if not _rate_limited(exc):
					raise self._qrmi_error(
						exc, "QRMI task_status failed") from exc
				# A cancel that ended the wait is the loop's to handle.
				if rate_limit.wait(at_least=wait) or rate_limit.cancelled():
					continue
				raise self._timed_out(
					job_id, timeout,
					"its status polls were rate-limited" if state is None
					else f"last status {state!r}, then rate-limited",
					credential=credential) from exc
			rate_limit.retries = 0
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
			if deadline is not None and time.monotonic() >= deadline:
				raise self._timed_out(
					job_id, timeout, f"last status {state!r}",
					credential=credential)
			pause = max(wait, 0.0)
			if deadline is not None:
				pause = min(pause, max(deadline - time.monotonic(), 0.0))
			if cancel_event is not None:
				cancel_event.wait(pause)
			else:
				time.sleep(pause)
			wait = next_poll_wait(wait, backoff)

	def _timed_out(self, job_id, timeout, last, credential=None):
		# Stop a job that ran past its timeout, and the error that says so.
		stop_error = self._stop_task(job_id, credential=credential)
		stopped = (
			f"task_stop failed: {stop_error}" if stop_error
			else "it was stopped")
		return DEFwExecutionError(
			f"QRMI job {job_id} timed out after {timeout}s ({last}), "
			f"and {stopped}")

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

	# --- task timing / metadata (from run_circuit's job records) ----------

	def get_task_timing(self, cid=None):
		job = self._jobs.get(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"timing": job.get("timing") or {}}

	def get_task_metadata(self, cid=None):
		job = self._jobs.get(cid)
		return {
			"cid": job.get("cid"),
			"job_id": job.get("id"),
			"status": job.get("status"),
			"shots": job.get("shots"),
			"backend": self._descriptor.get("provider", "iqm")}
