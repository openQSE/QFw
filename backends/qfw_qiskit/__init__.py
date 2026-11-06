import importlib

# Each name loads on first use, so a compile job imports no DEFw.
_MODULES = {
	'QFwBackend': '.qfw_simulator',
	'QFwSamplerV2': '.qfw_sampler',
	'QFwEstimatorV2': '.qfw_estimator',
	'QFwJob': '.qfw_job',
	'QFwTelemetry': '.qfw_telemetry',
	'get_isa_marker': '.qfw_metadata',
	'get_qubit_mapping': '.qfw_metadata',
	'mark_isa': '.qfw_metadata',
	'set_qubit_mapping': '.qfw_metadata',
}

__all__ = list(_MODULES)


def __getattr__(name):
	if name not in _MODULES:
		raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
	return getattr(importlib.import_module(_MODULES[name], __name__), name)


def __dir__():
	return sorted(set(globals()) | set(__all__))
