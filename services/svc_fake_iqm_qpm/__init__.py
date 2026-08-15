from .svc_qpm import QPM
import defw
import util.qpm.startup as qpm_startup

SERVICE_NAME = 'QPM'
SERVICE_DESC = 'Deterministic fake IQM QPM for admission and scheduler tests'

svc_info = {
	'name': SERVICE_NAME,
	'module': __name__,
	'description': SERVICE_DESC,
	'version': 1.0,
	'properties': {
		'provider': 'fake-iqm',
		'target_id': 'fake-iqm-20q',
		'device_id': 'fake-iqm-20q',
		'num_qubits': 20,
		'max_shots': 10000,
		'selector': {
			'name': 'fake-iqm-20q',
			'resources': ['fake-iqm-20q', 'FAKE-IQM-20q'],
			'aliases': ['fake-iqm', 'iqm-test'],
			'test_backend': True,
		},
	}
}

service_classes = [QPM]


def initialize():
	qpm_startup.initialize_qpm_service(
		defw,
		"Fake IQM QPM Initialized Successfully",
	)


def uninitialize():
	qpm_startup.uninitialize_qpm_service("Fake IQM QPM shutdown")
