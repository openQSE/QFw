from .qfw_simulator import QFwBackend
from .qfw_sampler import QFwSamplerV2
from .qfw_estimator import QFwEstimatorV2
from .qfw_job import QFwJob
from .qfw_metadata import get_qubit_mapping, set_qubit_mapping
from .qfw_telemetry import QFwTelemetry

__all__ = [
	'QFwBackend',
	'QFwBackendV2',
	'QFwSamplerV2',
	'QFwEstimatorV2',
	'QFwJob',
	'QFwJobV2',
	'QFwTelemetry',
	'get_qubit_mapping',
	'set_qubit_mapping',
]


def __getattr__(name):
	if name == 'QFwBackendV2':
		from .qfw_backend_v2 import QFwBackendV2

		return QFwBackendV2
	if name == 'QFwJobV2':
		from .qfw_job_v2 import QFwJobV2

		return QFwJobV2
	raise AttributeError(name)
