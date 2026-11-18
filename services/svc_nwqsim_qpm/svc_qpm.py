import logging
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM
from util.qpm.util_circuit import set_max_qubits_pp

MAX_NWQSIM_QUBITS = 32
MAX_NWQSIM_SHOTS = 1_000_000
# The OpenQASM 2 instructions circuit_runner.nwqsim runs: DEFAULT_GATES in
# NWQ-Sim's qasm/src/parser_util.hpp, and measure. Its parser skips any other
# name with only an "Undefined instruction" line on stdout, so the circuit
# runs without it.
NWQSIM_INSTRUCTIONS = (
	"u", "u3", "u2", "u1", "x", "y", "z", "h",
	"s", "sdg", "t", "tdg", "sx",
	"rx", "ry", "rz",
	"cz", "cx", "cy", "ch",
	"ccx", "crx", "cry", "crz", "cu", "ecr", "cu1", "cu3",
	"reset", "swap", "cswap",
	"id", "delay", "ri", "p", "cs", "csdg", "ct", "ctdg", "csx", "cp",
	"rzz", "rxx", "ryy", "measure",
)


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		super().__init__(QRC(start=start), start=start)
		set_max_qubits_pp(MAX_NWQSIM_QUBITS)
		self.configure_device_profile(
			max_qubits=MAX_NWQSIM_QUBITS,
			max_shots=MAX_NWQSIM_SHOTS)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC, svc_info
		from api_qpm_common import QPMType, QPMCapability
		info = self.query_helper(
			QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_STATEVECTOR,
			SERVICE_NAME, SERVICE_DESC,
			properties={
				**svc_info["properties"],
				"num_qubits": MAX_NWQSIM_QUBITS,
				"max_shots": MAX_NWQSIM_SHOTS,
				"instructions": list(NWQSIM_INSTRUCTIONS),
			})
		logging.debug(f"NWQSIM {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		info['qfw_backend'] = 'circuit_runner.nwqsim'
		return info

	def get_backend_info(self, lib=None, token=None, reservation_id=None):
		return {
			'backend': 'nwqsim',
			'metadata_supported': False,
		}

	def get_device_info(self, lib=None, token=None):
		return {
			'backend': 'nwqsim',
			'metadata_supported': False,
		}

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			'backend': 'nwqsim',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			'backend': 'nwqsim',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		return {
			'backend': 'nwqsim',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}
