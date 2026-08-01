from api_qpm import QPMExecution


svc_info = {
	'name': 'QPMExecution',
	'description': 'Quantum Platform Manager execution API',
	'version': 1.0,
	'category': 'execution',
}

service_classes = [QPMExecution]


def initialize():
	pass


def uninitialize():
	pass
