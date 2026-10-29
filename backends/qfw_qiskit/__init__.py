"""QFw Qiskit integrations with lazy optional-runtime imports."""

from importlib import import_module


_EXPORTS = {
	"QFwBackend": ("qfw_simulator", "QFwBackend"),
	"QFwBackendV2": ("qfw_backend_v2", "QFwBackendV2"),
	"QFwSamplerV2": ("qfw_sampler", "QFwSamplerV2"),
	"QFwEstimatorV2": ("qfw_estimator", "QFwEstimatorV2"),
	"QFwJob": ("qfw_job", "QFwJob"),
	"QFwJobV2": ("qfw_job_v2", "QFwJobV2"),
	"QFwTelemetry": ("qfw_telemetry", "QFwTelemetry"),
	"get_qubit_mapping": ("qfw_metadata", "get_qubit_mapping"),
	"set_qubit_mapping": ("qfw_metadata", "set_qubit_mapping"),
	"qhw_to_qiskit_target": ("qhw_target", "qhw_to_qiskit_target"),
}

__all__ = tuple(_EXPORTS)


def __getattr__(name):
	try:
		module_name, attribute = _EXPORTS[name]
	except KeyError as exc:
		raise AttributeError(name) from exc
	value = getattr(import_module(f"{__name__}.{module_name}"), attribute)
	globals()[name] = value
	return value
