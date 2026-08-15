from .svc_qpm import QPM
import defw
import util.qpm.startup as qpm_startup

SERVICE_NAME = 'QPM'
SERVICE_DESC = 'Quantum Platform Manager (QRMI/QDMI shim)'

# This is used by the infrastructure to display information about
# the service module. The name is also used as a key through out the
# infrastructure. Without it the service module will not load.
svc_info = {
	'name': SERVICE_NAME,
	'module': __name__,
	'description': SERVICE_DESC,
	'version': 1.0,
	'properties': {
		'provider': 'shim',
		'num_qubits': 20,
	}
}

# This is used by the infrastructure to define all the service classes.
# Each class should be a separate service. Each class should implement the
# following methods:
#	query()
#	reserve()
#	release()
service_classes = [QPM]


def initialize():
	qpm_startup.initialize_qpm_service(
		defw,
		"Shim QPM Initialized Successfully",
	)


def uninitialize():
	qpm_startup.uninitialize_qpm_service("Shim QPM shutdown")
