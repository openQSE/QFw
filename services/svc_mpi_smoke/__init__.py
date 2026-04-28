from .svc_mpi_smoke import MPISmoke

SERVICE_NAME = 'MPISmoke'
SERVICE_DESC = 'MPI-backed smoke test service for QFw'

svc_info = {
	'name': SERVICE_NAME,
	'module': __name__,
	'description': SERVICE_DESC,
	'version': 1.0,
	'instance_mode': 'singleton',
}

service_classes = [MPISmoke]


def initialize():
	return None


def uninitialize():
	return None
