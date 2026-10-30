import logging
import os
from defw_exception import DEFwExecutionError
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM
from util.qpm.util_circuit import set_max_qubits_pp
from util.circuit_payload import qiskit_circuit_formats, OPENQASM2, PULSER
from util import isa

MAX_SHIM_QUBITS = 1024
MAX_SHIM_SHOTS = 10000


def _technology(descriptor):
	# The qhw technology family the resource's device belongs to, for the
	# directory record. A Braket device says so in its ARN; the IQM and IBM
	# devices the shim serves are superconducting, and Pasqal's neutral-atom.
	provider = str(descriptor.get("provider") or "").lower()
	if provider == "aws":
		from util.braket_transcode import technology_for_arn
		return technology_for_arn(descriptor.get("provider_device_id"))
	if provider in ("iqm", "ibm"):
		return "superconducting"
	if provider == "pasqal":
		return "neutral-atom"
	return None


def _capability(provider):
	# The technology bit a client looks the service up by: neutral-atom for
	# a Pasqal device, and the superconducting bit the shim always advertised
	# for every other, whatever its technology. What a QPM reads is its
	# circuit_formats, which a client checks before it sends anything.
	from api_qpm_common import QPMCapability
	if provider == "pasqal":
		return QPMCapability.QPM_CAP_NEUTRAL_ATOM
	return QPMCapability.QPM_CAP_SUPERCONDUCTING


class QPM(UTIL_QPM):
	def __init__(self, start=True, admission_context_factory=None,
		     scheduler_context_factory=None):
		super().__init__(
			QRC(start=start), max_ppn=1, start=start,
			admission_context_factory=admission_context_factory,
			scheduler_context_factory=scheduler_context_factory)
		set_max_qubits_pp(MAX_SHIM_QUBITS)
		self.configure_device_profile(
			max_qubits=MAX_SHIM_QUBITS,
			max_shots=self._max_shots(),
			time_span_ns=60_000_000_000,
			max_provider_queue_depth=self._max_concurrent_jobs())

	def _max_shots(self):
		# A device-access max-shots is what this service admits and what the
		# Braket profile refuses above; the shim's own ceiling otherwise.
		from .descriptor import resolve_descriptor
		value = resolve_descriptor().get("max_shots")
		return int(value) if value not in (None, "") else MAX_SHIM_SHOTS

	def _max_concurrent_jobs(self):
		# A device-access max-concurrent-jobs is how many of this device's
		# circuits may be at the provider at once, from submission to result,
		# for a provider that answers a burst with HTTP 429. It is the device
		# profile's max_provider_queue_depth, so the controller holds the
		# rest in its queue and dispatches the next as one finishes. The
		# controller also applies the operator's max_inflight, 1 unless an
		# operator raises it, and the smaller of the two wins. Unset, the
		# device adds no limit of its own (0).
		from .descriptor import resolve_descriptor
		descriptor = resolve_descriptor()
		value = descriptor.get("max_concurrent_jobs")
		if value in (None, ""):
			return 0
		text = str(value).strip()
		if isinstance(value, bool) or not text.isdigit() or int(text) < 1:
			raise DEFwExecutionError(
				f"max-concurrent-jobs for device {descriptor.get('id')!r} "
				f"must be a whole number of jobs, at least 1, not {value!r}. "
				"Leave it out for no device limit")
		return int(text)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm_common import QPMType
		from .descriptor import resolve_descriptor
		properties = dict(svc_info.get('properties', {}))
		device_id = os.environ.get('QFW_QPU_DEVICE_ID')
		if device_id:
			properties['device_id'] = device_id
		# Both drivers read circuits through Qiskit, so QPY as well as
		# OpenQASM 2, unless the service's own configuration says otherwise.
		# A Pasqal device runs Pulser sequences instead, and nothing else.
		descriptor = resolve_descriptor()
		provider = str(descriptor.get("provider") or "").lower()
		formats = ({"circuit_formats": [PULSER]} if provider == "pasqal"
			else qiskit_circuit_formats())
		for key, value in formats.items():
			properties.setdefault(key, value)
		# IBM devices reject OpenQASM 2 at run time (_run_ibm_circuit requires
		# QPY). Remove it from the declaration so a client that cannot write QPY
		# gets a clear error on format negotiation rather than a silent fallback
		# that only fails once the circuit reaches the device.
		if provider == "ibm":
			formats = properties.get("circuit_formats", [])
			properties["circuit_formats"] = [
				f for f in formats if f != OPENQASM2]
		# The directory record's `provider` stays 'shim', which is how clients
		# find a shim service. The resource's own provider and technology ride
		# beside it, and a device whose library does not say up front (the
		# record is built before any session opens) takes its qubit count
		# from device-access config. The capability bits follow the
		# technology (_capability).
		if provider:
			properties["device_provider"] = provider
		technology = _technology(descriptor)
		if technology:
			properties["technology"] = technology
		description_library = self._description_library()
		if description_library:
			properties["description_library"] = description_library
		if descriptor.get("num_qubits") not in (None, ""):
			properties["num_qubits"] = int(descriptor["num_qubits"])
		info = self.query_helper(
			QPMType.QPM_TYPE_HARDWARE,
			_capability(provider),
			SERVICE_NAME, SERVICE_DESC,
			properties=properties)
		logging.debug(f"shim {SERVICE_DESC}: {info}")
		return info

	def _description_library(self):
		# Read with getattr because the mock tests build a QPM without running
		# __init__.
		frontend = getattr(getattr(self, "qrc", None), "frontend", None)
		return frontend.description_library() if frontend is not None else None

	def circuit_stages(self):
		return isa.circuit_stages(self._provider())

	def isa_device_id(self):
		# What query() publishes as device_id. Publishing it for a device
		# named only by config would change the service's id.
		from .descriptor import resolve_descriptor
		return (os.environ.get('QFW_QPU_DEVICE_ID')
			or resolve_descriptor().get("id"))

	def prepare_circuit(self, info):
		# The qhw backend tag names the provider this resource belongs to, as
		# its device-access entry declares it: iqm for the q20, aws for a
		# Braket device. Only the simulator QPMs read the key back (to find
		# their circuit runner), so for the shim it is provenance.
		info['qfw_backend'] = self._provider()
		return info

	def _provider(self):
		# Resolved once from the device-access config. Read with getattr
		# because the mock tests build a QPM without running __init__.
		provider = getattr(self, "_descriptor_provider", None)
		if provider is None:
			from .descriptor import resolve_descriptor
			provider = str(
				resolve_descriptor().get("provider") or "iqm").lower()
			self._descriptor_provider = provider
		return provider

	def capability_map(self, token=None):
		return self.qrc.capability_map()

	def get_backend_info(self, lib=None, token=None, reservation_id=None):
		# A client that names its reservation reads the device as the
		# reservation's credential sees it, as its circuits run. A QPU's
		# specs need a login, which the service may not hold for itself.
		return self.qrc.get_backend_info(lib=lib,
			credential=self._reservation_credential(reservation_id, token))

	def _reservation_credential(self, reservation_id, token=None):
		# The credential bound to the reservation, resolved here from the
		# controller rather than taken from the client, after the checks
		# execution and cancel_task make (a reservation that is closing or
		# does not match is refused), in the shape execution gets
		# (controller.attach_provider_credential).
		if reservation_id is None:
			return None
		from util.qpm.request import parse_execution_request
		request = parse_execution_request(
			{}, reservation_id=reservation_id, token=token)
		self.require_managed_execution(request)
		reservation_id = request.context.reservation_id
		record = self.controller.provider_credential_for_reservation(
			reservation_id, operation="get_backend_info")
		return dict(record.get("secret") or {}, reservation_id=reservation_id)

	def get_device_info(self, lib=None, token=None):
		return self.qrc.get_device_info(lib=lib)

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		return self.qrc.get_dynamic_backend_info(calibration_set_id, lib=lib)

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return self.qrc.get_calibration_snapshot(calibration_set_id, lib=lib)

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		return self.qrc.get_coupling_graph(calibration_set_id, lib=lib)
