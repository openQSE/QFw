# What differs per provider in QrmiDriver. The driver owns the QRMI resource
# and its job lifecycle; a profile owns what the provider's device reports and
# how a circuit reaches it. Imports stay lazy so services can start without
# the provider's libraries.

from .base_driver import pasqal_backend_info
from defw_exception import DEFwExecutionError
from util import instrumentation
import json
import os


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

DEFAULT_SHOTS = 1024

def num_shots(info: dict, default: int = DEFAULT_SHOTS) -> int:
	"""Return the shot count from a circuit info dict.

	Accepts both the canonical ``num_shots`` key and the legacy ``shots``
	alias, falling back to *default* when neither is present.
	"""
	return int(info.get("num_shots") or info.get("shots") or default)


def _identifies_caller(credential):
	# Whether a credential names whose access the resource opens with: a key,
	# or, outside a reservation, the user whose key _access looks up. Only
	# then does it replace the settings the environment holds. Every
	# circuit's credential carries its reservation id: the no-secret
	# provider's nothing else, the entitlement provider's a user but no key.
	# Neither names an identity to open with, so SPANK's and the operator's
	# settings still apply.
	credential = credential or {}
	return bool(credential.get("api_key") or credential.get("token")
		or (credential.get("user") and not credential.get("reservation_id")))


class QrmiProfile:
	provider = None
	resource_types = ()
	capabilities = frozenset()

	def __init__(self, descriptor=None):
		self._descriptor = dict(descriptor or {})

	def serves(self, call, driver_capabilities):
		return call in driver_capabilities

	def is_complete_target(self, target_data):
		# Whether a target() payload is whole enough to cache.
		return True

	def device_info(self, driver):
		raise NotImplementedError

	def coupling_graph(self, driver):
		raise NotImplementedError

	def calibration_snapshot(self, driver):
		raise NotImplementedError

	def dynamic_backend_info(self, driver):
		raise NotImplementedError

	def backend_info(self, driver, credential=None):
		raise NotImplementedError

	def run_circuit(self, driver, source):
		raise NotImplementedError

	def vendor_timing(self, driver, job_id, credential=None):
		# The provider's own account of the job, or {} when it has none.
		return {}

	def resource_settings(self, driver, type_name, backend, credential=None):
		# (settings, required, kind) QRMI opens a resource of this type with,
		# or None when the profile resolves nothing for the type and the
		# environment already carries it, from the SPANK plugin or the
		# operator.
		return None

	def ensure_env(self, driver, type_name, alias, credential=None):
		# Write those settings into the process environment, for a qrmi with
		# no from_config().
		return None


class IqmQrmiProfile(QrmiProfile):
	provider = "iqm"
	resource_types = ("IQMServer",)

	def device_info(self, driver):
		from qhw_iqm import normalize_device
		return normalize_device(
			driver._arch_raw(), device_id=driver._device_id())

	def coupling_graph(self, driver):
		from qhw_iqm import normalize_coupling
		return normalize_coupling(
			driver._arch_raw(), device_id=driver._device_id())

	def calibration_snapshot(self, driver):
		# target() carries the IQM calibration_set + quality_metrics; feed them
		# (with the dynamic architecture) to qhw-iqm — the same normalizer the
		# native svc_iqm_qpm path uses — to build a qhw-calibration-v1 record.
		# Selecting a specific calibration_set_id is a follow-up; this returns
		# the resource's current/default calibration.
		from qhw_iqm import normalize_calibration
		target = driver._target()
		raw = {
			"dynamic_architecture":
				target.get("dynamic_quantum_architecture") or {},
			"calibration_set": target.get("calibration_set") or {},
			"quality_metric_set": target.get("quality_metrics") or {},
		}
		return normalize_calibration(raw, device_id=driver._device_id())

	def dynamic_backend_info(self, driver):
		# The dynamic architecture as-is, matching the native svc_iqm_qpm shape
		# (a provider dict, not a qhw record).
		return {
			"backend": self._descriptor.get("provider", "iqm"),
			"metadata_supported": True,
			"dynamic_architecture":
				driver._target().get("dynamic_quantum_architecture") or {},
		}

	def backend_info(self, driver, credential=None):
		# Native composite shape (mirrors svc_iqm_qpm.get_backend_info): the
		# provider fields plus an embedded qhw device record. The static
		# architecture is empty against QRMI older than 0.22.0, which did not
		# report it at all.
		from qhw_iqm import normalize_device
		target = driver._target(credential=credential)
		dynamic = target.get("dynamic_quantum_architecture") or {}
		return {
			"backend": self._descriptor.get("provider", "iqm"),
			"metadata_supported": True,
			"static_architecture": driver._static_arch(target),
			"active_qubits": dynamic.get("qubits") or [],
			"calibration_set_id": dynamic.get("calibration_set_id"),
			"qhw_device": normalize_device(
				driver._arch_raw(credential), device_id=driver._device_id()),
		}

	def run_circuit(self, driver, source):
		return driver._run_iqm_circuit(source)

	def resource_settings(self, driver, type_name, backend, credential=None):
		if type_name != "IQMServer":
			return None
		settings, required = driver._iqm_isa_settings(
			backend, credential=credential)
		return settings, required, None

	def ensure_env(self, driver, type_name, alias, credential=None):
		if type_name == "IQMServer":
			driver._ensure_iqm_isa_env(alias, credential=credential)

	def vendor_timing(self, driver, job_id, credential=None):
		# QRMI returns the measurements alone from task_result, and the job's
		# timeline, the server's timestamp per phase, only as the text of
		# task_logs, which util.iqm_timeline reads.
		from util.iqm_timeline import durations_from_task_logs
		return durations_from_task_logs(
			driver._task_logs(job_id, credential=credential))


class IbmQrmiProfile(QrmiProfile):
	provider = "ibm"
	resource_types = tuple(IBM_RESOURCE_ENV_KINDS)

	def is_complete_target(self, target_data):
		# IBM target() returns null for configuration or properties on
		# failure, and a payload cached then would stick after IBM recovers.
		return (
			isinstance(target_data, dict)
			and target_data.get("configuration") is not None
			and target_data.get("properties") is not None)

	@staticmethod
	def _configuration(driver, credential=None):
		configuration = driver._target(
			credential=credential).get("configuration") or {}
		if not configuration:
			raise DEFwExecutionError("QRMI failed to retrieve the backend data")
		return configuration

	def device_info(self, driver):
		from qhw_ibm import normalize_device
		return normalize_device(
			self._configuration(driver), device_id=driver._device_id())

	def coupling_graph(self, driver):
		from qhw_ibm import normalize_coupling
		return normalize_coupling(
			self._configuration(driver), device_id=driver._device_id())

	def calibration_snapshot(self, driver):
		# The calibration normalizer reads the properties sub-dict
		# (qubits, gates, last_update_date).
		from qhw_ibm import normalize_calibration
		properties = driver._target().get("properties") or {}
		if not properties:
			raise DEFwExecutionError("QRMI failed to retrieve the backend data")
		return normalize_calibration(properties, device_id=driver._device_id())

	def dynamic_backend_info(self, driver):
		# IBM target() has no live dynamic state; configuration and
		# properties are static snapshots with no calibration_set_id or
		# active qubit list equivalent.
		return {
			"backend": "ibm",
			"metadata_supported": False,
			"dynamic_architecture": {},
		}

	def backend_info(self, driver, credential=None):
		from qhw_ibm import normalize_device
		target = driver._target(credential=credential)
		configuration = target.get("configuration") or {}
		if not configuration:
			raise DEFwExecutionError("QRMI failed to retrieve the backend data")
		qhw_device = normalize_device(
			configuration, device_id=driver._device_id())
		qubits_len = len(qhw_device.get("qubits", []))
		n_qubits = configuration.get("n_qubits", qubits_len)
		# The raw configuration and properties together are what
		# _build_backend_target converts to the ISA target, so a client
		# that builds its own target gets both here.
		return {
			"backend": "ibm",
			"metadata_supported": True,
			"static_architecture": configuration,
			"backend_properties": target.get("properties") or None,
			"active_qubits": list(range(n_qubits)),
			"calibration_set_id": None,
			"qhw_device": qhw_device,
		}

	def run_circuit(self, driver, source):
		return self._run_ibm_circuit(driver, source)

	def resource_settings(self, driver, type_name, backend, credential=None):
		kind = IBM_RESOURCE_ENV_KINDS.get(type_name)
		if not kind:
			return None
		settings, required = self._ibm_settings(
			driver, kind, backend, credential=credential)
		return settings, required, kind

	def ensure_env(self, driver, type_name, alias, credential=None):
		kind = IBM_RESOURCE_ENV_KINDS.get(type_name)
		if kind:
			driver._ensure_ibm_env(kind, alias, credential=credential)

	def _ibm_settings(self, driver, kind, backend, credential=None):
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
		if _identifies_caller(credential):
			# No fallback on this path. A credential that cannot be resolved
			# has to fail rather than leave another reservation's endpoint or
			# key in place.
			access = driver._access(credential=credential)
			settings[endpoint_key] = access.get("base_url")
			settings[apikey_key] = access.get("token")
		else:
			endpoint = current(endpoint_key)
			apikey = current(apikey_key)
			if not (endpoint and apikey):
				try:
					access = driver._access(credential=credential)
				except DEFwExecutionError:
					# Fall through to the missing-setting report, which names
					# what to set. It is more actionable than a failure to
					# resolve device access the caller may not be relying on.
					access = {}
			settings[endpoint_key] = endpoint or access.get("base_url")
			settings[apikey_key] = apikey or access.get("token")

		settings[iam_endpoint_key] = current(iam_endpoint_key) or str(
			os.environ.get("QFW_IBM_IAM_ENDPOINT")
			or driver._descriptor.get("iam_endpoint")
			or driver._descriptor.get("iam-endpoint")
			or IBM_DEFAULT_IAM_ENDPOINT)

		crn = (dict(credential or {}).get("service_crn")
			or os.environ.get("QFW_IBM_SERVICE_CRN")
			or access.get("service_crn")
			or driver._descriptor.get("service_crn")
			or driver._descriptor.get("service-crn"))
		if _identifies_caller(credential):
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
			settings.update(self._object_storage_settings(driver, 
				prefix, backend, access, credential))
			settings.update(self._job_timeout_settings(driver, backend))
			required += [
				f"{prefix}_{suffix}" for suffix in OBJECT_STORAGE_REQUIRED]
			required.append(JOB_TIMEOUT_SETTING)
		return settings, required

	def _ensure_ibm_env(self, driver, kind, alias, credential=None):
		# Write an IBM family into the process environment, for a qrmi with no
		# from_config().
		backend = alias.split(",")[0]
		settings, required = self._ibm_settings(driver, 
			kind, backend, credential=credential)
		self._ensure_settings_env(driver, backend, settings, required, kind)

	def _ensure_settings_env(self, driver, backend, settings, required, kind):
		driver._write_settings_env(backend, settings)
		missing = [key for key in required
				if not os.environ.get(f"{backend}_{key}")]
		if missing:
			raise driver._missing_settings_error(backend, missing, kind=kind)

	def _object_storage_settings(self, driver, prefix, backend, access,
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
				or driver._descriptor.get(key)
				or driver._descriptor.get(key.replace("_", "-")))
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
			if not _identifies_caller(credential):
				settings[setting_key] = (
					os.environ.get(f"{backend}_{setting_key}")
					or (str(value) if value else None))
				continue
			# With a credential these are replaced, not filled in, for the
			# reason the endpoint and key are: one reservation's key would
			# otherwise serve the next.
			settings[setting_key] = str(value) if value else None
		return settings

	def _job_timeout_settings(self, driver, backend):
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
			or driver._descriptor.get(JOB_TIMEOUT_KEY)
			or driver._descriptor.get(JOB_TIMEOUT_KEY.replace("_", "-"))
			or DEFAULT_JOB_TIMEOUT_SECONDS)
		return {JOB_TIMEOUT_SETTING: str(value)}

	def _run_ibm_circuit(self, driver, source):
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
			target, target_config_json = self._build_backend_target(driver, source)
		with instrumentation.qpm_transpile():
			payload = self._build_ibm_payload(driver, source, target)
		result_json, job = driver._run_sampler_payload(payload, source)

		# Update last_job with IBM measurements.
		if job is not None:
			results = result_json.get("results") or []
			data = results[0].get("data") or {}
			job["measurements"] = data
			driver._jobs.put(job)

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
		return normalize_result(raw, device_id=driver._device_id())

	def _build_backend_target(self, driver, source):
		# Fetch the raw backend configuration and properties via QRMI, then
		# convert them to a Qiskit Target used for ISA compilation.
		try:
			from qiskit_ibm_runtime.utils.backend_converter import convert_to_target
			from qiskit_ibm_runtime.models import BackendProperties, BackendConfiguration
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to import qiskit_ibm_runtime models: {exc}") from exc

		target_data = driver._target(credential=getattr(source, "provider_credential", None))
		target_config_json = target_data.get("configuration")
		target_props_json = target_data.get("properties")

		if not target_config_json or not target_props_json:
			raise DEFwExecutionError("QRMI failed to retrieve the backend data")

		backend_config = BackendConfiguration.from_dict(target_config_json)
		backend_props = BackendProperties.from_dict(target_props_json)
		target = convert_to_target(backend_config, backend_props)
		return target, target_config_json

	def _build_ibm_payload(self, driver, source, target):
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

		qrmi = driver._resource()
		return qrmi.Payload.QiskitPrimitive(input=input_json, program_id="sampler")


class PasqalQrmiProfile(QrmiProfile):
	provider = "pasqal"
	resource_types = ("PasqalCloud", "PasqalLocal")
	# No qhw normalizer reads a Pulser device, so the qhw introspection calls
	# are a gap for it rather than an IQM parse of the wrong payload.
	capabilities = frozenset({
		"get_backend_info",
		"run_circuit",
		"get_task_timing",
		"get_task_metadata",
	})

	def serves(self, call, driver_capabilities):
		return call in self.capabilities

	def backend_info(self, driver, credential=None):
		# target() is Pasqal Cloud's device-spec entries, read for each call
		# rather than through the target cache, so a client builds against the
		# current calibration. An emulator's are public; a QPU's need a login,
		# the reservation's credential when the client names its reservation
		# and the service's otherwise (_pasqal_cloud_settings).
		try:
			entries = json.loads(
				driver._qpu(credential=credential).target().value)
		except Exception as exc:
			raise driver._qrmi_error(
				exc, "failed to read QRMI target()") from exc
		return pasqal_backend_info(
			str(driver._qc_alias(credential=credential) or "").split(",")[0],
			entries)

	def run_circuit(self, driver, source):
		return driver._run_pasqal_sequence(source)

	def resource_settings(self, driver, type_name, backend, credential=None):
		if type_name != "PasqalCloud":
			return None
		settings, required = driver._pasqal_cloud_settings(
			backend, credential=credential)
		return settings, required, "PASQAL"

	def ensure_env(self, driver, type_name, alias, credential=None):
		if type_name == "PasqalCloud":
			# Such a qrmi also reads $HOME/.pasqal/config and the unprefixed
			# PASQAL_USERNAME, whoever's they are, beside a user's token.
			raise DEFwExecutionError(
				"the shim opens Pasqal Cloud resources with "
				"QuantumResource.from_config(), which keeps a user's token "
				"apart from the service's login; install qrmi 0.25.0 or "
				"later")


PROFILES = {
	profile.provider: profile
	for profile in (IqmQrmiProfile, IbmQrmiProfile, PasqalQrmiProfile)}


def profile_for(descriptor):
	# A provider QRMI has no profile for takes the IQM one, as the driver
	# always has.
	provider = str((descriptor or {}).get("provider") or "iqm").lower()
	return PROFILES.get(provider, IqmQrmiProfile)(descriptor)


def profile_for_resource_type(type_name, descriptor):
	# The profile that resolves the settings a resource of this type opens
	# with, whichever provider the descriptor names. None for a type no
	# profile serves.
	for profile in PROFILES.values():
		if type_name in profile.resource_types:
			return profile(descriptor)
	return None
