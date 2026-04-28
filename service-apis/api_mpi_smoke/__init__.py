from .api_mpi_smoke import *  # noqa: F401,F403

svc_info = {
	'name': 'MPISmoke',
	'description': 'MPI-backed smoke test service for QFw',
	'version': 1.0,
}

service_classes = [MPISmoke]  # noqa: F405


def initialize():
	pass


def uninitialize():
	pass
