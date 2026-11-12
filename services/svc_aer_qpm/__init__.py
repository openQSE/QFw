from .settings import aer_settings_from_service_entry
from .svc_qpm import QPM
import defw
import util.qpm.startup as qpm_startup

SERVICE_NAME = 'QPM'
SERVICE_DESC = 'Quantum Platform Manager running Qiskit Aer, noiseless or under a named device\'s noise'

svc_info = {
	'name': SERVICE_NAME,
	'module': __name__,
	'description': SERVICE_DESC,
	'version': 1.0,
	'instance_mode': 'singleton',
	'properties': {
		'provider': 'aer',
	},
}

service_classes = [QPM]


def accelerator(service_entry):
	if aer_settings_from_service_entry(service_entry).device == "GPU":
		return "gpu"
	return None


def initialize():
	qpm_startup.initialize_qpm_service(
		defw,
		"Aer QPM Initialized Successfully",
	)


def uninitialize():
	qpm_startup.uninitialize_qpm_service("Aer QPM shutdown")
