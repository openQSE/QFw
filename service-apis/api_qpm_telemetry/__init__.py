from .api_qpm_telemetry import QPMTelemetry


svc_info = {
	'name': 'QPMTelemetry',
	'description': 'Quantum Platform Manager telemetry and discovery API',
	'version': 1.0,
	'category': 'telemetry',
}

service_classes = [QPMTelemetry]


def initialize():
	pass


def uninitialize():
	pass
