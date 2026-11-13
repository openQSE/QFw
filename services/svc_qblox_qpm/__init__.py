from .svc_qpm import QPM, QBLOX_MAX_QUBITS, QBLOX_MAX_SHOTS, QBLOX_TARGET_ID
import defw
import util.qpm.startup as qpm_startup

SERVICE_NAME = 'QPM'
SERVICE_DESC = 'Quantum Platform Manager for Qblox'

# This is used by the infrastructure to display information about
# the service module. The name is also used as a key through out the
# infrastructure. Without it the service module will not load.
svc_info = {
	'name': SERVICE_NAME,
	'module': __name__,
	'description': SERVICE_DESC,
	'version': 1.0,
	'instance_mode': 'singleton',
	'properties': {
		'provider': 'qblox',
		'target_id': QBLOX_TARGET_ID,
		'num_qubits': QBLOX_MAX_QUBITS,
		'max_shots': QBLOX_MAX_SHOTS,
		# No selector block: UTIL_QPM derives it from target_id,
		# provider and service name.
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
		"Qblox QPM Initialized Successfully",
	)


def uninitialize():
	qpm_startup.uninitialize_qpm_service("Qblox QPM shutdown")
