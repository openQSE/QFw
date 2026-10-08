import logging
import os
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM
from util.qpm.util_circuit import set_max_qubits_pp
from util.circuit_payload import qiskit_circuit_formats, OPENQASM2

MAX_SHIM_QUBITS = 1024
MAX_SHIM_SHOTS = 10000


def _technology(descriptor):
	# The qhw technology family the resource's device belongs to, for the
	# directory record. A Braket device says so in its ARN; the IQM and IBM
	# devices the shim serves are superconducting.
	provider = str(descriptor.get("provider") or "").lower()
	if provider == "aws":
		from util.braket_transcode import technology_for_arn
		return technology_for_arn(descriptor.get("provider_device_id"))
	if provider in ("iqm", "ibm"):
		return "superconducting"
	return None


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		super().__init__(QRC(start=start), max_ppn=1, start=start)
		set_max_qubits_pp(MAX_SHIM_QUBITS)
		self.configure_device_profile(
			max_qubits=MAX_SHIM_QUBITS,
			max_shots=self._max_shots(),
			time_span_ns=60_000_000_000)

	def _max_shots(self):
		# A device-access max-shots is what this service admits and what the
		# Braket profile refuses above; the shim's own ceiling otherwise.
		from .descriptor import resolve_descriptor
		value = resolve_descriptor().get("max_shots")
		return int(value) if value not in (None, "") else MAX_SHIM_SHOTS

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm_common import QPMType, QPMCapability
		from .descriptor import resolve_descriptor
		properties = dict(svc_info.get('properties', {}))
		device_id = os.environ.get('QFW_QPU_DEVICE_ID')
		if device_id:
			properties['device_id'] = device_id
		# Both drivers read circuits through Qiskit, so QPY as well as
		# OpenQASM 2, unless the service's own configuration says otherwise.
		for key, value in qiskit_circuit_formats().items():
			properties.setdefault(key, value)
		# IBM devices reject OpenQASM 2 at run time (_run_ibm_circuit requires
		# QPY). Remove it from the declaration so a client that cannot write QPY
		# gets a clear error on format negotiation rather than a silent fallback
		# that only fails once the circuit reaches the device.
		descriptor = resolve_descriptor()
		if descriptor.get("provider", "").lower() == "ibm":
			formats = properties.get("circuit_formats", [])
			properties["circuit_formats"] = [
				f for f in formats if f != OPENQASM2]
		# The directory record's `provider` stays 'shim', which is how clients
		# find a shim service. The resource's own provider and technology ride
		# beside it, and a device whose library does not say up front (the
		# record is built before any session opens) takes its qubit count
		# from device-access config. QPMCapability has no bits for a
		# simulator or a trapped-ion device, so the capability bits stay as
		# they are; `technology` is where that reads.
		provider = str(descriptor.get("provider") or "").lower()
		if provider:
			properties["device_provider"] = provider
		technology = _technology(descriptor)
		if technology:
			properties["technology"] = technology
		if descriptor.get("num_qubits") not in (None, ""):
			properties["num_qubits"] = int(descriptor["num_qubits"])
		info = self.query_helper(
			QPMType.QPM_TYPE_HARDWARE,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			SERVICE_NAME, SERVICE_DESC,
			properties=properties)
		logging.debug(f"shim {SERVICE_DESC}: {info}")
		return info

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

	def get_backend_info(self, lib=None, token=None):
		return self.qrc.get_backend_info(lib=lib)

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
