from .api_qpm_admission_policy_config import QPMAdmissionPolicyConfig


svc_info = {
	'name': 'QPMAdmissionPolicyConfig',
	'description': 'Quantum Platform Manager admission policy API',
	'version': 1.0,
	'category': 'admission-policy',
}

service_classes = [QPMAdmissionPolicyConfig]


def initialize():
	pass


def uninitialize():
	pass
