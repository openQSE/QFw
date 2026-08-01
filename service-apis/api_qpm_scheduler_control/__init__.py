from api_qpm import QPMSchedulerControl


svc_info = {
	'name': 'QPMSchedulerControl',
	'description': 'Quantum Platform Manager scheduler control API',
	'version': 1.0,
	'category': 'scheduler',
}

service_classes = [QPMSchedulerControl]


def initialize():
	pass


def uninitialize():
	pass
