from .api_qpm_admission_control import QPMAdmissionControl


svc_info = {
	'name': 'QPMAdmissionControl',
	'description': 'Quantum Platform Manager admission control API',
	'version': 1.0,
	'category': 'admission',
}

service_classes = [QPMAdmissionControl]


def initialize():
	pass


def uninitialize():
	pass
