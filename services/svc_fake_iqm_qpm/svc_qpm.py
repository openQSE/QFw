import logging

from .svc_qrc import QRC
from util.qpm.util_circuit import set_max_qubits_pp
from util.qpm.util_qpm import UTIL_QPM

FAKE_IQM_PROVIDER = "fake-iqm"
FAKE_IQM_TARGET_ID = "fake-iqm-20q"
FAKE_IQM_MAX_QUBITS = 20
FAKE_IQM_MAX_SHOTS = 10_000


def fake_iqm_20q_profile(device_id):
	return {
		"device_id": device_id,
		"external_device_id": FAKE_IQM_TARGET_ID,
		"max_qubits": FAKE_IQM_MAX_QUBITS,
		"max_shots": FAKE_IQM_MAX_SHOTS,
		"time_span_ns": 60_000_000_000,
		"baseline": {
			"qubit_count": 4,
			"depth": 10,
			"one_q_gate_count": 10,
			"two_q_gate_count": 5,
			"measurement_count": 4,
			"shots": 128,
		},
		"one_q_gate_ns": 20,
		"two_q_gate_ns": 100,
		"measurement_ns": 1000,
		"one_q_gate_transfer_ns": 1,
		"two_q_gate_transfer_ns": 4,
		"measurement_transfer_ns": 10,
		"compile_ns": 1000,
		"control_overhead_ns": 200,
		"provider_overhead_ns": 300,
		"total_credits": 64,
		"device_rate": 512,
		"concurrent_jobs": 8,
		"default_ttl_ns": 60_000_000_000,
		"max_provider_queue_depth": 8,
	}


class QPM(UTIL_QPM):
	def __init__(self, start=True, admission_context_factory=None,
		     scheduler_context_factory=None):
		qrc = QRC(start=start, target_id=FAKE_IQM_TARGET_ID)
		super().__init__(
			qrc,
			max_ppn=1,
			start=start,
			target_id=FAKE_IQM_TARGET_ID,
			admission_context_factory=admission_context_factory,
			scheduler_context_factory=scheduler_context_factory)
		set_max_qubits_pp(FAKE_IQM_MAX_QUBITS)
		device_id = self.controller.canonicalize_external_id(
			"device_id", FAKE_IQM_TARGET_ID)
		self.configure_device_profile(
			profile=fake_iqm_20q_profile(device_id))

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm import QPMCapability, QPMType

		properties = dict(svc_info.get('properties', {}))
		properties.update({
			"provider": FAKE_IQM_PROVIDER,
			"target_id": FAKE_IQM_TARGET_ID,
			"device_id": FAKE_IQM_TARGET_ID,
			"resource_id": FAKE_IQM_TARGET_ID,
			"num_qubits": FAKE_IQM_MAX_QUBITS,
			"max_shots": FAKE_IQM_MAX_SHOTS,
			"test_backend": True,
		})
		info = self.query_helper(
			QPMType.QPM_TYPE_HARDWARE,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			SERVICE_NAME, SERVICE_DESC,
			properties=properties)
		logging.debug(f"Fake IQM {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		info = dict(info)
		info["qfw_backend"] = FAKE_IQM_PROVIDER
		info.setdefault("target_device_id", FAKE_IQM_TARGET_ID)
		info.setdefault("num_qubits", FAKE_IQM_MAX_QUBITS)
		info.setdefault("num_shots", info.get("shots", 1024))
		return info

	def get_backend_info(self, lib=None, token=None):
		return {
			"backend": FAKE_IQM_PROVIDER,
			"target_id": FAKE_IQM_TARGET_ID,
			"metadata_supported": True,
			"test_backend": True,
		}

	def get_device_info(self, lib=None, token=None):
		return {
			"backend": FAKE_IQM_PROVIDER,
			"target_id": FAKE_IQM_TARGET_ID,
			"num_qubits": FAKE_IQM_MAX_QUBITS,
			"max_shots": FAKE_IQM_MAX_SHOTS,
			"metadata_supported": True,
			"test_backend": True,
		}

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		info = self.get_device_info(lib=lib, token=token)
		info["calibration_set_id"] = calibration_set_id
		return info

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			"backend": FAKE_IQM_PROVIDER,
			"target_id": FAKE_IQM_TARGET_ID,
			"calibration_set_id": calibration_set_id,
			"test_backend": True,
		}

	def get_coupling_graph(self, calibration_set_id=None, lib=None, token=None):
		return {
			"backend": FAKE_IQM_PROVIDER,
			"target_id": FAKE_IQM_TARGET_ID,
			"calibration_set_id": calibration_set_id,
			"couplings": [
				[index, index + 1]
				for index in range(FAKE_IQM_MAX_QUBITS - 1)
			],
			"test_backend": True,
		}

	def test(self):
		return "****Fake IQM QPM Test Successful****"
