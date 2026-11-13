import logging
import os

from .svc_qrc import QRC
from .util_qblox import device_qubit_count, strip_quotes
from util.qpm.util_circuit import set_max_qubits_pp
from util.qpm.util_qpm import UTIL_QPM
from util.circuit_payload import qiskit_circuit_formats

QBLOX_PROVIDER = "qblox"

# One module, two identities: which one this process is comes from whether
# a cluster address is configured.
QBLOX_IS_DUMMY = not strip_quotes(os.environ.get("QBLOX_CLUSTER_IP"))
QBLOX_TARGET_ID = os.environ.get(
	"QFW_QBLOX_TARGET_ID",
	"qblox-dummy" if QBLOX_IS_DUMMY else "qblox-cluster")

# Read at import: UTIL_QPM needs a static count before HardwareAgent exists,
# and the agent empties "elements" when constructed.
QBLOX_MAX_QUBITS = int(
	os.environ.get("QFW_QBLOX_MAX_QUBITS") or device_qubit_count() or 0)
QBLOX_MAX_SHOTS = int(os.environ.get("QFW_QBLOX_MAX_SHOTS", "10000"))


def qblox_device_profile(device_id):
	# Timings are placeholders at the right order of magnitude, not
	# measurements. QBLOX_PROFILE_STAGES=1 records real per-stage timings
	# through get_task_timing(); derive these coefficients from that.
	return {
		"device_id": device_id,
		"external_device_id": QBLOX_TARGET_ID,
		"max_qubits": QBLOX_MAX_QUBITS,
		"max_shots": QBLOX_MAX_SHOTS,
		"time_span_ns": 60_000_000_000,
		"baseline": {
			"qubit_count": min(QBLOX_MAX_QUBITS, 2) or 1,
			"depth": 10,
			"one_q_gate_count": 10,
			"two_q_gate_count": 5,
			"measurement_count": min(QBLOX_MAX_QUBITS, 2) or 1,
			"shots": 128,
		},
		"one_q_gate_ns": 20,
		"two_q_gate_ns": 100,
		"measurement_ns": 1000,
		"one_q_gate_transfer_ns": 1,
		"two_q_gate_transfer_ns": 4,
		"measurement_transfer_ns": 10,
		"compile_ns": 1_000_000,
		"control_overhead_ns": 200,
		"provider_overhead_ns": 300,
		"total_credits": 64,
		"device_rate": 512,
		# Not negotiable: the agent and instrument coordinator are global
		# device operations. The client holds a matching lock.
		"concurrent_jobs": 1,
		"default_ttl_ns": 60_000_000_000,
		"max_provider_queue_depth": 8,
	}


class QPM(UTIL_QPM):
	def __init__(self, start=True, admission_context_factory=None,
		     scheduler_context_factory=None):
		qrc = QRC(start=start, target_id=QBLOX_TARGET_ID)
		super().__init__(
			qrc,
			max_ppn=1,
			start=start,
			target_id=QBLOX_TARGET_ID,
			admission_context_factory=admission_context_factory,
			scheduler_context_factory=scheduler_context_factory)
		set_max_qubits_pp(QBLOX_MAX_QUBITS)
		device_id = self.controller.canonicalize_external_id(
			"device_id", QBLOX_TARGET_ID)
		self.configure_device_profile(profile=qblox_device_profile(device_id))

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm_common import QPMCapability, QPMType

		properties = dict(svc_info.get('properties', {}))
		properties.update({
			"provider": QBLOX_PROVIDER,
			"target_id": QBLOX_TARGET_ID,
			"device_id": os.environ.get(
				'QFW_QPU_DEVICE_ID', QBLOX_TARGET_ID),
			"num_qubits": QBLOX_MAX_QUBITS,
			"max_shots": QBLOX_MAX_SHOTS,
		})
		# QPY is preferred over QASM 2: the basis rewrite transpiles, and
		# QASM 2 round-trips lose precision on arbitrary-angle rx/ry.
		for key, value in qiskit_circuit_formats().items():
			properties.setdefault(key, value)
		# No QFW_CAP_STATEVECTOR: Qblox returns thresholded shots, so the
		# VQE example correctly skips this backend.
		info = self.query_helper(
			QPMType.QPM_TYPE_SIMULATOR if QBLOX_IS_DUMMY
			else QPMType.QPM_TYPE_HARDWARE,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			SERVICE_NAME, SERVICE_DESC,
			properties=properties)
		logging.debug(f"Qblox {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		info = dict(info)
		info["qfw_backend"] = QBLOX_PROVIDER
		info.setdefault("target_device_id", QBLOX_TARGET_ID)
		info.setdefault("num_qubits", QBLOX_MAX_QUBITS)
		info.setdefault("num_shots", info.get("shots", 1024))
		return info

	def get_backend_info(self, lib=None, token=None):
		return self.qrc.get_backend_info()

	def get_device_info(self, lib=None, token=None):
		return self.qrc.get_device_info()

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		return self.qrc.get_dynamic_backend_info(calibration_set_id)

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return self.qrc.get_calibration_snapshot(calibration_set_id)

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		return self.qrc.get_coupling_graph(calibration_set_id)
