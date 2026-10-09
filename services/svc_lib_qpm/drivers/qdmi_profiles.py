# Provider profiles for the QDMI driver.
#
# QDMI is one C interface, but each device library behind it fills the
# vendor-defined parts differently: how a session is opened and from which
# settings, which program format a job takes, what the CUSTOM session, job
# and device slots mean, and how a counts histogram is keyed. The driver
# (qdmi_driver.py) keeps the session and job lifecycle in one place and asks
# the resource's profile for those parts. A profile is chosen from the
# resource descriptor's provider (descriptor.py), so adding a provider means
# adding a profile here and nothing in the driver.
#
# The IQM profile is the driver's behavior from before profiles existed,
# moved here unchanged: QDMI-on-IQM opens on a server URL, an API token and
# a quantum computer alias, takes an IQM_JSON program, and publishes the
# active calibration set id in device CUSTOM1.
#
# The Braket profile drives MQSC's Amazon Braket device library: a session is
# one device ARN, a job is one Braket quantum task, the program is Braket's
# OpenQASM 3, results come back through S3, and the AWS identity is the
# service process's own (the AWS SDK credential chain), never a token QFw
# holds. Its CUSTOM1 is the supported-operations list, not a calibration id.
#
# Every import of a device library or of mqt.core is deferred to the call
# that needs it, so a service builds and routes where the libraries are
# absent, and so a cancel that arrives before submission needs none of them.

from . import fomac_normalize
from defw_exception import DEFwExecutionError
import os

DEFAULT_PROVIDER = "iqm"
DEFAULT_TIMEOUT_SECONDS = 300.0

# The one implementation name iqm_architecture gives every gate. QDMI does not
# report IQM's implementation names, and only metrics lookups use them.
IQM_IMPLEMENTATION = "qdmi"


def iqm_architecture(topology, calibration_set_id):
	# The IQM dynamic quantum architecture rebuilt from FoMaC topology, for
	# util.iqm_transcode to transpile against. QDMI-on-IQM builds its
	# operations from IQM's own architecture, so each operation's name and
	# loci are IQM's gate name and loci. Each gate gets a single
	# implementation holding all its loci, and the server runs its default.
	#
	# What cannot be rebuilt gets the bare qubit list instead, which
	# build_iqm_circuit can only serialize from, so only an already-native
	# circuit runs:
	#   - no calibration set id, which the architecture requires;
	#   - no measure gate, without which IQMBackendBase raises a KeyError
	#     rather than a DEFwExecutionError the caller would fall back on;
	#   - a locus naming a component that is not a qubit. That is a
	#     computational resonator, and FoMaC does not say which sites are
	#     resonators, so a device with them is not modeled here.
	qubits = [str(qubit) for qubit in topology.get("qubits") or []]
	bare = {"qubits": qubits}
	operations = topology.get("operations") or {}
	if not calibration_set_id or not operations.get("measure"):
		return bare
	known = set(qubits)
	gates = {}
	for name, loci in operations.items():
		loci = [[str(component) for component in locus]
			for locus in loci or [] if locus]
		if not loci:
			continue
		if any(component not in known
		       for locus in loci for component in locus):
			return bare
		gates[name] = {
			"implementations": {IQM_IMPLEMENTATION: {"loci": loci}},
			"default_implementation": IQM_IMPLEMENTATION,
			"override_default_implementation": {},
		}
	return {
		"calibration_set_id": str(calibration_set_id),
		"qubits": qubits,
		"computational_resonators": [],
		"gates": gates,
	}


class QdmiProfile:
	# The descriptor provider this profile serves.
	provider = None
	# The package that supplies the device library, named in import errors.
	requires = "a QDMI device library"
	# The QDMI device-property CUSTOM slot that carries the provider's
	# calibration set identifier, as a mqt.core.qdmi.CustomProperty attribute
	# name, or None when the provider publishes none. fomac_normalize reads
	# it; a wrong slot would read some other vendor-defined value as an id.
	calibration_set_slot = None

	def __init__(self, descriptor=None):
		self._descriptor = dict(descriptor or {})

	def access(self, credential=None):
		"""The settings this resource's session opens with (a dict).

		credential is the reservation's bound provider credential, the
		circuit's provider_credential, or None for a call made outside a
		reservation."""
		raise NotImplementedError

	def definition(self, access):
		"""The mqt.core.qdmi.driver.DeviceDefinition to register."""
		raise NotImplementedError

	def open_kwargs(self, access):
		"""Keyword arguments for mqt.core.qdmi.driver.open_device."""
		raise NotImplementedError

	def describe(self, access):
		# What to log once the session is open.
		return access.get("qc_alias") or access.get("base_url")

	def encode(self, driver, source, info, device):
		"""Encode a circuit for the device library.

		Returns (program, program_format, measurement): the program as the
		library takes it, the mqt.core.qdmi.ProgramFormat member NAME (the
		driver resolves it, so this needs no mqt.core), and whatever
		result_counts needs to key the histogram back onto the circuit, or
		None when the library's keys already match.
		"""
		raise NotImplementedError

	def job_kwargs(self, info):
		# Extra keyword arguments for Device.submit_job (the job CUSTOM slots).
		return {}

	def check_shots(self, shots):
		# A provider with a shot cap rejects here, before anything is billed.
		return None

	def timeout_seconds(self, info):
		# How long run_circuit waits for the job before cancelling it.
		return float(info.get("timeout", DEFAULT_TIMEOUT_SECONDS))

	def result_counts(self, counts, measurement):
		# Key the device library's histogram the way the circuit's classical
		# bits are ordered. Identity where they already agree.
		return counts

	def technology(self):
		# The qhw-device-v1 technology family, or None to leave it unset.
		return None


class IqmQdmiProfile(QdmiProfile):
	provider = "iqm"
	requires = "iqm-qdmi"
	# QDMI-on-IQM publishes the active calibration set's UUID in CUSTOM1,
	# verified equal to QRMI's dynamic_quantum_architecture.calibration_set_id
	# on the same q20.
	calibration_set_slot = "CUSTOM1"

	def access(self, credential=None):
		# Resolve connection settings for the QDMI device, in the order the
		# QRMI driver uses (QrmiDriver._access). The reservation's credential
		# comes first: the service runs as one account for every user, so
		# anything else would open the session as the service's account.
		# Then the env vars the native svc_iqm_qpm honors, then the shared
		# device-access config, resolved for the credential's user.
		credential = dict(credential or {})
		provider = self._descriptor.get("provider", DEFAULT_PROVIDER)
		device_id = credential.get("device_id") or self._descriptor.get("id")
		provider_device_id = (
			credential.get("provider_device_id")
			or credential.get("quantum_computer")
			or self._descriptor.get("provider_device_id")
			or self._descriptor.get("provider-device-id"))
		base_url = credential.get("url") or os.environ.get("QFW_QC_URL")
		token = (
			credential.get("api_key")
			or credential.get("token")
			or os.environ.get("QFW_API_KEY"))
		if not (base_url and token):
			try:
				from util.device_access import resolve_device_access
				cfg = resolve_device_access(
					provider=provider,
					device_id=device_id,
					user=credential.get("user"),
					credential_hint=credential.get("credential_hint"),
					credential_handle=credential.get("credential_handle"))
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

	def definition(self, access):
		# The IQM device library is registered under the stable device ID
		# iqm-qdmi publishes.
		from iqm.qdmi import (IQM_QDMI_DEVICE_ID, IQM_QDMI_LIBRARY_PATH,
				IQM_QDMI_PREFIX)
		from mqt.core.qdmi.driver import DeviceDefinition
		return DeviceDefinition(
			IQM_QDMI_DEVICE_ID,
			str(IQM_QDMI_LIBRARY_PATH),
			IQM_QDMI_PREFIX)

	def open_kwargs(self, access):
		# qc_alias is passed as the device session's custom2 parameter (as
		# iqm.qdmi.qiskit does).
		return {
			"base_url": access.get("base_url"),
			"token": access.get("token"),
			"custom2": access.get("qc_alias"),
		}

	def encode(self, driver, source, info, device):
		# Transcode to an IQM circuit with the shared util and serialize it as
		# the single-circuit IQM_JSON program QDMI-on-IQM takes. Note the
		# QRMI/QDMI difference: QDMI's IQM_JSON program is a SINGLE circuit,
		# and QDMI-on-IQM wraps it into the run request (circuits, shots,
		# calibration set) itself, whereas QRMI submits the whole run request.
		# The transcode needs the device's architecture to transpile a circuit
		# that is not already native, h and cx for instance. QDMI has no raw
		# dynamic-architecture dict like QRMI's target(), so it is rebuilt
		# from FoMaC topology and the calibration set id (iqm_architecture).
		# Serialization stays a driver method, so a test can stand in for it.
		from util.iqm_transcode import build_iqm_circuit
		mapping = info.get("iqm_qubit_mapping") or info.get("qubit_mapping")
		topo = fomac_normalize.extract_topology(device)
		dynamic = iqm_architecture(topo, fomac_normalize._calibration_set_id(
			device, self.calibration_set_slot))
		iqm_circuit = build_iqm_circuit(source, dynamic, mapping)
		return driver._serialize_program(iqm_circuit), "IQM_JSON", None


class BraketQdmiProfile(QdmiProfile):
	provider = "aws"
	requires = "amazon-braket-qdmi"
	# Braket publishes no calibration set id. Its device CUSTOM1 holds the
	# supportedOperations list (see util.braket_transcode).
	calibration_set_slot = None
	# The library's generic catalogue entry; it takes the ARN and Region from
	# the session. A concrete catalogue id (amazon.braket.sv1, ...) can be
	# named instead, through qdmi-device-id in device-access config.
	DEFAULT_QDMI_DEVICE_ID = "amazon.braket.default"
	ARN_PREFIX = "arn:aws:braket:"

	def access(self, credential=None):
		# Everything comes from the descriptor, so from device-access config,
		# and none of it is a secret. There is no token: the library
		# authenticates through the AWS SDK's default credential chain, and
		# QFw's entitlement credential provider decides who may use the
		# device without holding a key (util.qpm.credentials). So the
		# reservation's credential holds nothing a session opens with.
		arn = (
			self._descriptor.get("provider_device_id")
			or self._descriptor.get("provider-device-id"))
		if not arn or not str(arn).startswith(self.ARN_PREFIX):
			raise DEFwExecutionError(
				"a Braket device needs its device ARN as provider-device-id "
				f"in device-access config, got {arn!r}")
		return {
			"base_url": str(arn),
			"qdmi_device_id": (
				self._descriptor.get("qdmi_device_id")
				or self.DEFAULT_QDMI_DEVICE_ID),
			"region": self._descriptor.get("aws_region") or None,
			"reservation_arn": self._descriptor.get("reservation_arn") or None,
		}

	def definition(self, access):
		from amazon.braket.qdmi import (AMAZON_BRAKET_QDMI_LIBRARY_PATH,
				AMAZON_BRAKET_QDMI_PREFIX)
		from mqt.core.qdmi.driver import DeviceDefinition
		return DeviceDefinition(
			access["qdmi_device_id"],
			str(AMAZON_BRAKET_QDMI_LIBRARY_PATH),
			AMAZON_BRAKET_QDMI_PREFIX)

	def open_kwargs(self, access):
		# The library's session parameters are aliases of QDMI's: DEVICEARN is
		# BASEURL, REGION is CUSTOM2 and RESERVATION_ARN is CUSTOM3
		# (amazon-braket-qdmi-device/constants.hpp). The Region defaults to
		# the one in the ARN, and a reservation ARN makes the device status
		# ignore public execution windows.
		kwargs = {"base_url": access["base_url"]}
		if access.get("region"):
			kwargs["custom2"] = access["region"]
		if access.get("reservation_arn"):
			kwargs["custom3"] = access["reservation_arn"]
		return kwargs

	def describe(self, access):
		return access.get("base_url")

	def encode(self, driver, source, info, device):
		# Braket's OpenQASM 3, in the gate names this device accepts. The
		# measurement map comes back so result_counts can key the histogram
		# onto the circuit's classical bits.
		from util.braket_transcode import (accepted_operation_names,
				to_braket_qasm3)
		from util.iqm_transcode import load_qiskit_circuit
		circuit = load_qiskit_circuit(source)
		program, measurement = to_braket_qasm3(
			circuit, accepted_operation_names(device))
		return program, "QASM3", measurement

	def job_kwargs(self, info):
		# OUTPUTS3URI is job CUSTOM1 and RESERVATION_ARN is job CUSTOM3.
		# Without a URI the library uses the account's standard regional
		# bucket, which needs STS and bucket-management permissions on first
		# use; a site that provisions its bucket names it here.
		kwargs = {}
		uri = self._descriptor.get("s3_results_uri")
		if uri:
			kwargs["custom1"] = str(uri)
		reservation = self._descriptor.get("reservation_arn")
		if reservation:
			kwargs["custom3"] = str(reservation)
		return kwargs

	def check_shots(self, shots):
		# Every Braket task is billed, so a device can carry a shot cap in
		# device-access config and anything above it is refused here, before
		# a task exists.
		cap = self._descriptor.get("max_shots")
		if cap in (None, ""):
			return None
		cap = int(cap)
		if int(shots) > cap:
			raise DEFwExecutionError(
				f"{shots} shots is over the {cap}-shot cap for device "
				f"{self._descriptor.get('id')!r} (max-shots in device-access "
				"config)")
		return None

	def timeout_seconds(self, info):
		# A Braket QPU queues tasks behind other customers', so the wait the
		# driver allows before cancelling comes from the device's
		# job-timeout-seconds. A timeout the client sends still wins.
		default = self._descriptor.get("job_timeout_seconds")
		if default in (None, ""):
			default = DEFAULT_TIMEOUT_SECONDS
		return float(info.get("timeout", default))

	def result_counts(self, counts, measurement):
		from util.braket_transcode import remap_counts
		return remap_counts(counts, measurement)

	def technology(self):
		from util.braket_transcode import technology_for_arn
		return technology_for_arn(
			self._descriptor.get("provider_device_id")
			or self._descriptor.get("provider-device-id"))


PROFILES = {
	IqmQdmiProfile.provider: IqmQdmiProfile,
	BraketQdmiProfile.provider: BraketQdmiProfile,
}


def profile_for(descriptor):
	"""The profile for a resource descriptor's provider (default iqm)."""
	provider = str(
		(descriptor or {}).get("provider") or DEFAULT_PROVIDER).lower()
	profile = PROFILES.get(provider)
	if profile is None:
		raise DEFwExecutionError(
			f"the QDMI driver has no profile for provider {provider!r}; it "
			f"knows {sorted(PROFILES)}")
	return profile(descriptor)
