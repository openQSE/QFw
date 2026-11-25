from .svc_qpm import QPM
import defw
import util.qpm.startup as qpm_startup

SERVICE_NAME = 'QPM'
SERVICE_DESC = 'Backline QPM: reserves QEC controllers, decoders and transports'

svc_info = {
	'name': SERVICE_NAME,
	'module': __name__,
	'description': SERVICE_DESC,
	'version': 1.0,
	'instance_mode': 'singleton',
	'properties': {
		'provider': 'backline',
		'target_id': 'backline-local',
		'device_id': 'backline-local',
		'selector': {
			'name': 'backline-local',
			'resources': ['backline-local'],
			'aliases': ['backline'],
		},
	}
}

service_classes = [QPM]


def initialize():
	qpm_startup.initialize_qpm_service(
		defw,
		"Backline QPM Initialized Successfully",
	)


def uninitialize():
	qpm_startup.uninitialize_qpm_service("Backline QPM shutdown")
