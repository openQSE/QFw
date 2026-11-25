import os

from .svc_qpm import QPM, DEFAULT_EMULATED_DEVICE
import defw
import util.qpm.startup as qpm_startup
from util.circuit_payload import qiskit_circuit_formats

SERVICE_NAME = 'QPM'
SERVICE_DESC = 'Calibrated Pauli-frame proxy sampler QPM for circuits too wide for noisy simulation'

_device = os.environ.get('QFW_QPU_DEVICE_ID') or DEFAULT_EMULATED_DEVICE

svc_info = {
	'name': SERVICE_NAME,
	'module': __name__,
	'description': SERVICE_DESC,
	'version': 1.0,
	'instance_mode': 'singleton',
	'properties': {
		'provider': 'proxy',
		'target_id': _device,
		'device_id': _device,
		'selector': {
			'name': _device,
			'resources': [_device],
			'aliases': ['proxy'],
		},
	}
}

service_classes = [QPM]


def import_qiskit_on_the_startup_thread():
	qiskit_circuit_formats()


def initialize():
	import_qiskit_on_the_startup_thread()
	qpm_startup.initialize_qpm_service(
		defw,
		"Proxy QPM Initialized Successfully",
	)


def uninitialize():
	qpm_startup.uninitialize_qpm_service("Proxy QPM shutdown")
