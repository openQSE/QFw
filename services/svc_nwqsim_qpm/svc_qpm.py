import logging
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM, openqasm_2_program_inputs
from util.qpm.util_circuit import set_max_qubits_pp

MAX_NWQSIM_QUBITS = 32
MAX_NWQSIM_SHOTS = 1_000_000


def _nodes():
	return [str(index) for index in range(MAX_NWQSIM_QUBITS)]


def _single_qubit_loci(nodes):
	return [[node] for node in nodes]


def _two_qubit_loci(nodes):
	return [[left, right] for left in nodes for right in nodes if left != right]


def _qhw_device():
	nodes = _nodes()
	return {
		"schema": "qhw-device-v1",
		"provider": "nwqsim",
		"device": {"id": "nwqsim", "num_qubits": len(nodes)},
		"qubits": [
			{"id": node, "index": index}
			for index, node in enumerate(nodes)
		],
	}


def _qhw_coupling():
	nodes = _nodes()
	single = _single_qubit_loci(nodes)
	pairs = _two_qubit_loci(nodes)
	return {
		"schema": "qhw-coupling-v1",
		"provider": "nwqsim",
		"device": {"id": "nwqsim"},
		"coupling": {
			"directed": True,
			"nodes": nodes,
			"edges": pairs,
		},
		"operations": [
			{"name": "rz", "arity": 1, "supported_loci": single},
			{"name": "sx", "arity": 1, "supported_loci": single},
			{"name": "x", "arity": 1, "supported_loci": single},
			{"name": "cx", "arity": 2, "supported_loci": pairs},
			{"name": "measure", "arity": 1,
			 "supported_loci": single},
			{"name": "reset", "arity": 1, "supported_loci": single},
		],
	}


class QPM(UTIL_QPM):
	PROGRAM_INPUTS = tuple(openqasm_2_program_inputs(native=True))

	def __init__(self, start=True):
		super().__init__(QRC(start=start), start=start)
		set_max_qubits_pp(MAX_NWQSIM_QUBITS)
		self.configure_device_profile(
			max_qubits=MAX_NWQSIM_QUBITS,
			max_shots=MAX_NWQSIM_SHOTS)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC
		from api_qpm_common import QPMType, QPMCapability
		info = self.query_helper(
			QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_STATEVECTOR,
			SERVICE_NAME, SERVICE_DESC,
			properties={
				"provider": "nwqsim",
				"device_id": "nwqsim",
				"num_qubits": MAX_NWQSIM_QUBITS,
				"max_shots": MAX_NWQSIM_SHOTS,
			})
		logging.debug(f"NWQSIM {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		info['qfw_backend'] = 'circuit_runner.nwqsim'
		return info

	def get_backend_info(self, lib=None, token=None, reservation_id=None):
		return {
			'backend': 'nwqsim',
			'metadata_supported': True,
		}

	def get_device_info(self, lib=None, token=None):
		return _qhw_device()

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			'backend': 'nwqsim',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return None

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		return _qhw_coupling()
