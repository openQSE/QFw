import logging
import os

from .svc_qrc import QRC
from util.circuit_payload import qiskit_circuit_formats
from util.proxy_sampler.device import ibm_backend_info, load_emulated_device
from util.proxy_sampler.sampler import ProxySampler
from util.qpm.util_circuit import set_max_qubits_pp
from util.qpm.util_qpm import UTIL_QPM

PROXY_PROVIDER = "proxy"
DEFAULT_EMULATED_DEVICE = "FakeFez"
MEMORY_LIMIT_ENV = "QFW_PROXY_MAX_MEMORY_MB"
DEFAULT_MAX_MEMORY_MB = 4096
MAX_PROXY_SHOTS = 1_000_000


def max_memory_mb():
	"""The ideal source's memory limit in MiB: the service setting, QFW_PROXY_MAX_MEMORY_MB."""
	return int(os.environ.get(MEMORY_LIMIT_ENV) or DEFAULT_MAX_MEMORY_MB)


class QPM(UTIL_QPM):
	def __init__(self, start=True, admission_context_factory=None,
		     scheduler_context_factory=None, backend=None):
		self.device_id = os.environ.get("QFW_QPU_DEVICE_ID") or DEFAULT_EMULATED_DEVICE
		self.backend = backend or load_emulated_device(self.device_id)
		self.num_qubits = self.backend.num_qubits
		self.max_memory_mb = max_memory_mb()
		self._backend_info = None
		sampler = ProxySampler(self.backend, max_memory_mb=self.max_memory_mb)
		super().__init__(
			QRC(sampler, target_id=self.device_id, start=start),
			max_ppn=1,
			start=start,
			target_id=self.device_id,
			admission_context_factory=admission_context_factory,
			scheduler_context_factory=scheduler_context_factory)
		set_max_qubits_pp(self.num_qubits)
		self.configure_device_profile(
			max_qubits=self.num_qubits, max_shots=MAX_PROXY_SHOTS)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm_common import QPMCapability, QPMType

		properties = dict(svc_info.get('properties', {}))
		properties.update({
			"provider": PROXY_PROVIDER,
			"target_id": self.device_id,
			"device_id": self.device_id,
			"num_qubits": self.num_qubits,
			"max_shots": MAX_PROXY_SHOTS,
			"max_memory_mb": self.max_memory_mb,
			# A job may carry info["logical_circuit"] and, as info["bases"],
			# ask for one record per Pauli basis of one prepared state.
			"logical_circuit": True,
			"bases_jobs": True,
		})
		properties.update(qiskit_circuit_formats())
		info = self.query_helper(
			QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			SERVICE_NAME, SERVICE_DESC,
			properties=properties)
		logging.debug(f"Proxy {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		info = dict(info)
		info["qfw_backend"] = PROXY_PROVIDER
		info.setdefault("num_qubits", self.num_qubits)
		info.setdefault("num_shots", info.get("shots", 1024))
		return info

	def get_backend_info(self, lib=None, token=None, reservation_id=None):
		if self._backend_info is None:
			self._backend_info = ibm_backend_info(self.backend)
		return {"backend": self.device_id, **self._backend_info}

	def get_device_info(self, lib=None, token=None):
		return {
			"backend": self.device_id,
			"num_qubits": self.num_qubits,
			"max_shots": MAX_PROXY_SHOTS,
			"metadata_supported": True,
		}

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		info = self.get_device_info(lib=lib, token=token)
		info["calibration_set_id"] = calibration_set_id
		return info

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			"backend": self.device_id,
			"calibration_set_id": calibration_set_id,
			"backend_properties": self.get_backend_info()["backend_properties"],
		}

	def get_coupling_graph(self, calibration_set_id=None, lib=None, token=None):
		return {
			"backend": self.device_id,
			"calibration_set_id": calibration_set_id,
			"couplings": self.get_backend_info()["static_architecture"]["coupling_map"],
		}
