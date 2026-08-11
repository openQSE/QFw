from api_qpm import QPMControl


svc_info = {
	'name': 'QPMControl',
	'description': 'Quantum Platform Manager privileged control API',
	'version': 1.0,
	'category': 'control',
}

service_classes = [QPMControl]


def initialize():
	pass


def uninitialize():
	pass
