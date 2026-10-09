"""
QFw telemetry bootstrap.

Owns OpenTelemetry provider setup, the deployment profile, and the sampling
policy described in docs/design/benchmarking.md. This package emits no spans
and no metrics of its own. Instrumentation sites import the accessors below.

Three things matter for callers:

1. Nothing here is required. If the OpenTelemetry SDK is not installed, or
   the profile is off, every accessor degrades to a no-op and QFw runs
   unchanged.

2. Turning tracing off does not make a call site free. A sampled-out span
   still costs microseconds in Python, because the context manager runs
   whatever the sampler decides. Cold paths may use the plain form:

	with tracer().start_as_current_span("qfw.qpm.receive"):
		...

   Hot paths must guard the call site so it does not execute at all:

	if transport_spans_enabled():
		with tracer().start_as_current_span("qfw.transport.rpc"):
			...

3. Attribute arguments are evaluated before a non-recording span discards
   them. Anything more expensive than a field read belongs behind
   span.is_recording().

Configuration is entirely by environment variable, so switching profiles is
a deployment change and never a code change:

The file profile writes OTLP/JSON, one export batch per line, so the same
files can be replayed into a collector later without a conversion step.

QFW_TELEMETRY            off | file | otlp        (default: off)
QFW_TELEMETRY_DIR        export directory for the file profile
QFW_TELEMETRY_SAMPLE     off | always | <ratio>   (default: off)
QFW_TELEMETRY_TRANSPORT  0 | 1                    (default: 0)
QFW_TELEMETRY_LOGS       off | error | warning | info | debug | all (default: off)
QFW_TELEMETRY_ENDPOINT   the collector's OTLP/HTTP base URL, otlp profile

The logs tier is the optional third signal. With a level set, the SDK's
logging handler joins the root logger, so every record Python's logging
carries at that level or above is exported with the trace and span ids of
whatever span is current when it is written. That is how a log line is
stitched to its job without a change to any call site. QFw's own lines
are mostly debug, so debug is the tier that shows a job's story; DEFw's
transport internals leave only with all.
"""

import logging
import os
import threading

TELEMETRY_ENV = "QFW_TELEMETRY"
TELEMETRY_DIR_ENV = "QFW_TELEMETRY_DIR"
TELEMETRY_SAMPLE_ENV = "QFW_TELEMETRY_SAMPLE"
TELEMETRY_TRANSPORT_ENV = "QFW_TELEMETRY_TRANSPORT"
TELEMETRY_ENDPOINT_ENV = "QFW_TELEMETRY_ENDPOINT"
TELEMETRY_LOGS_ENV = "QFW_TELEMETRY_LOGS"

LOG_LEVELS = {
	"error": logging.ERROR,
	"warning": logging.WARNING,
	"info": logging.INFO,
	"debug": logging.DEBUG,
	"all": logging.DEBUG,
}
LOGS_ALL = "all"

# Loggers whose records never leave through the logs tier: the SDK and the
# HTTP stack it exports with. A failed export logs a warning; exporting that
# warning would fail the same way, and a debug level would turn every HTTP
# connection into a record that opens another connection.
_LOG_SOURCES_KEPT_LOCAL = ("opentelemetry", "urllib3", "requests")

# DEFw's levels 30 to 35 are categories, not severities: CORE, WORKER,
# SERVICE, APP, RPC and STACKTRACE, registered by name. Four of them are the
# transport's own internals, hundreds of lines per job about work requests
# and RPC handling, with routine stack dumps. Those leave only with "all".
# SERVICE and APP are what QFw's services and applications write through
# DEFw, and go out at warning and above like a warning would. QFw's own
# code mostly logs at debug, so "debug" is the tier that tells a job's
# story without the transport's.
_DEFW_INTERNAL_CATEGORIES = (
	"DEFW_CORE", "DEFW_WORKER", "DEFW_RPC", "DEFW_STACKTRACE")

PROFILE_OFF = "off"
PROFILE_FILE = "file"
PROFILE_OTLP = "otlp"

SAMPLE_OFF = "off"
SAMPLE_ALWAYS = "always"

# Bumped when span names or attribute meanings change. Recorded on every
# resource so old telemetry stays interpretable. See the semantic conventions
# section of the design document.
CONVENTIONS_VERSION = 1

DEFAULT_TELEMETRY_DIRNAME = "qfw-telemetry"

# How often the always-on metrics tier exports, unless the standard
# OTEL_METRIC_EXPORT_INTERVAL says otherwise. The SDK's own default is a
# minute, which is a long time for a dashboard to wait and the whole window
# of metrics lost when a service is killed rather than stopped. One export
# every ten seconds costs nothing measurable.
DEFAULT_METRIC_EXPORT_INTERVAL_MS = 10_000
METRIC_EXPORT_INTERVAL_ENV = "OTEL_METRIC_EXPORT_INTERVAL"

# Bucket boundaries for the duration histograms, in seconds. The SDK's
# default boundaries (5, 10, 25, ... 10000) are sized for milliseconds, so
# every sub-second hop of a job would land in the first bucket and a quantile
# over them would say nothing. These run from a tenth of a millisecond, the
# cost of an RPC, to ten minutes, a long provider queue.
DURATION_BUCKETS = (
	0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1,
	0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 60.0, 120.0, 300.0, 600.0)

try:
	from opentelemetry import trace as _trace
	from opentelemetry import metrics as _metrics
	OTEL_AVAILABLE = True
except ImportError:
	_trace = None
	_metrics = None
	OTEL_AVAILABLE = False


class _State(object):
	"""Process-wide telemetry state. One instance, guarded by _LOCK."""

	def __init__(self):
		self.configured = False
		self.profile = PROFILE_OFF
		self.tracer_provider = None
		self.meter_provider = None
		self.tracer = None
		self.meter = None
		self.histograms = {}
		self.counters = {}
		self.streams = []
		self.transport_spans = False
		self.defw_hooks = False
		self.logger_provider = None
		self.log_handler = None
		self.logs_level = None


_STATE = _State()
_LOCK = threading.Lock()


class _NoopSpan(object):
	"""Stands in for a span when telemetry is off or the SDK is absent."""

	def __enter__(self):
		return self

	def __exit__(self, exc_type, exc, tb):
		return False

	def is_recording(self):
		return False

	def set_attribute(self, key, value):
		pass

	def set_attributes(self, attributes):
		pass

	def add_event(self, name, attributes=None):
		pass

	def record_exception(self, exception):
		pass

	def set_status(self, status, description=None):
		pass

	def end(self):
		pass


class _NoopTracer(object):
	def start_as_current_span(self, name, **kwargs):
		return _NoopSpan()

	def start_span(self, name, **kwargs):
		return _NoopSpan()


class _NoopInstrument(object):
	def record(self, amount, attributes=None):
		pass

	def add(self, amount, attributes=None):
		pass


class _NoopMeter(object):
	def create_histogram(self, name, unit="", description=""):
		return _NoopInstrument()

	def create_counter(self, name, unit="", description=""):
		return _NoopInstrument()


_NOOP_TRACER = _NoopTracer()
_NOOP_METER = _NoopMeter()
_NOOP_INSTRUMENT = _NoopInstrument()


def _env(name, default=""):
	return os.environ.get(name, default).strip()


def _profile():
	"""Resolve the deployment profile, defaulting to off."""
	value = _env(TELEMETRY_ENV, PROFILE_OFF).lower()
	if value in ("", "0", "no", "false", PROFILE_OFF):
		return PROFILE_OFF
	if value in (PROFILE_FILE, "1", "yes", "true"):
		return PROFILE_FILE
	if value == PROFILE_OTLP:
		return PROFILE_OTLP
	# An unrecognised profile is a deployment mistake. Failing closed keeps a
	# typo from silently disabling telemetry a benchmark run depends on, and
	# keeps it from silently enabling telemetry in production either.
	raise ValueError(
		f"{TELEMETRY_ENV} must be one of "
		f"'{PROFILE_OFF}', '{PROFILE_FILE}', '{PROFILE_OTLP}': got {value!r}")


def _logs_level():
	"""The logs tier's level as a logging constant, or None when off."""
	value = _env(TELEMETRY_LOGS_ENV, "off").lower()
	if value in ("", "0", "no", "false", "off"):
		return None
	if value in LOG_LEVELS:
		return LOG_LEVELS[value]
	raise ValueError(
		f"{TELEMETRY_LOGS_ENV} must be 'off' or one of "
		f"{', '.join(repr(name) for name in LOG_LEVELS)}: got {value!r}")


def _logs_keep_internals():
	"""True only for the "all" tier, which carries DEFw's transport chatter."""
	return _env(TELEMETRY_LOGS_ENV, "off").lower() == LOGS_ALL


def _export_dir():
	configured = _env(TELEMETRY_DIR_ENV)
	if configured:
		return configured
	# Node-local by default. Never a shared filesystem, because export
	# contention would perturb what is being measured.
	base = _env("DEFW_TMP_DIR") or _env("TMPDIR") or "/tmp"
	return os.path.join(base, DEFAULT_TELEMETRY_DIRNAME)


def _build_sampler():
	"""
	Build the sampler from QFW_TELEMETRY_SAMPLE.

	Parent-based in every case, so a sampling decision made at the run root
	propagates with traceparent and a sampled trace is complete across every
	node rather than sampled independently per service.
	"""
	from opentelemetry.sdk.trace.sampling import (
		ALWAYS_OFF, ALWAYS_ON, ParentBased, TraceIdRatioBased)

	value = _env(TELEMETRY_SAMPLE_ENV, SAMPLE_OFF).lower()
	if value in ("", SAMPLE_OFF, "0", "no", "false"):
		return ParentBased(root=ALWAYS_OFF)
	if value in (SAMPLE_ALWAYS, "1", "yes", "true", "on"):
		return ParentBased(root=ALWAYS_ON)
	try:
		ratio = float(value)
	except ValueError:
		raise ValueError(
			f"{TELEMETRY_SAMPLE_ENV} must be "
			f"'{SAMPLE_OFF}', '{SAMPLE_ALWAYS}', or a ratio "
			f"between 0 and 1: got {value!r}")
	if not 0.0 <= ratio <= 1.0:
		raise ValueError(
			f"{TELEMETRY_SAMPLE_ENV} ratio must be between 0 and 1: "
			f"got {ratio}")
	return ParentBased(root=TraceIdRatioBased(ratio))


def _build_resource(service_name, service_version, role, attributes=None):
	from opentelemetry.sdk.resources import Resource

	values = {}
	# Caller-supplied facts about this process, such as the device a QPM
	# serves. The fixed keys below win where they collide.
	for key, value in (attributes or {}).items():
		if value is not None:
			values[str(key)] = value
	values["service.name"] = service_name
	values["qfw.conventions.version"] = CONVENTIONS_VERSION
	if service_version:
		values["service.version"] = service_version
	if role:
		values["qfw.component.role"] = role
	slurm_job = _env("SLURM_JOB_ID")
	if slurm_job:
		values["qfw.slurm.job_id"] = slurm_job
	return Resource.create(values)


def _open_export_stream(service_name, kind):
	"""Open one node-local export file, named so parallel ranks do not collide."""
	directory = _export_dir()
	os.makedirs(directory, exist_ok=True)
	rank = _env("SLURM_PROCID") or _env("OMPI_COMM_WORLD_RANK") or "0"
	name = f"{service_name}-{rank}-{os.getpid()}.{kind}.jsonl"
	return open(os.path.join(directory, name), "a", encoding="utf-8")


def _file_span_processor(service_name):
	from opentelemetry.sdk.trace.export import BatchSpanProcessor

	from ._otlp_json import OtlpJsonFileSpanExporter

	stream = _open_export_stream(service_name, "spans")
	_STATE.streams.append(stream)
	# BatchSpanProcessor keeps the write off the calling thread, which is what
	# holds the per-span cost down, and it batches so the resource block is
	# written once per line rather than once per span.
	return BatchSpanProcessor(OtlpJsonFileSpanExporter(stream))


def _otlp_endpoint(signal_path):
	"""
	The OTLP/HTTP URL for one signal, from QFW_TELEMETRY_ENDPOINT, or None
	to let the exporter read the standard OTEL_EXPORTER_OTLP_* variables.

	The variable names the collector, http://host:4318, the way
	OTEL_EXPORTER_OTLP_ENDPOINT does, and the signal's path is appended
	here. An explicit endpoint handed to the exporter is used verbatim, so
	without this step the collector would answer 404 to every export. A
	value that already ends in the signal's path is used as given.
	"""
	endpoint = _env(TELEMETRY_ENDPOINT_ENV)
	if not endpoint:
		return None
	base = endpoint.rstrip("/")
	if base.endswith("/" + signal_path):
		return base
	return f"{base}/{signal_path}"


def _otlp_span_processor():
	from opentelemetry.sdk.trace.export import BatchSpanProcessor
	from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
		OTLPSpanExporter)

	# endpoint=None hands the choice to the exporter's own environment.
	exporter = OTLPSpanExporter(endpoint=_otlp_endpoint("v1/traces"))
	return BatchSpanProcessor(exporter)


def _otlp_log_processor():
	from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
	from opentelemetry.exporter.otlp.proto.http._log_exporter import (
		OTLPLogExporter)

	exporter = OTLPLogExporter(endpoint=_otlp_endpoint("v1/logs"))
	return BatchLogRecordProcessor(exporter)


def _file_log_processor(service_name):
	from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

	from ._otlp_json import OtlpJsonFileLogExporter

	stream = _open_export_stream(service_name, "logs")
	_STATE.streams.append(stream)
	return BatchLogRecordProcessor(OtlpJsonFileLogExporter(stream))


class _LogSourceFilter(logging.Filter):
	"""
	Keeps the exporter's own loggers out of the export, and DEFw's transport
	internals out of every tier but "all".
	"""

	def __init__(self, keep_internals):
		super().__init__()
		self._keep_internals = keep_internals

	def filter(self, record):
		name = record.name or ""
		if any(name == source or name.startswith(source + ".")
				for source in _LOG_SOURCES_KEPT_LOCAL):
			return False
		if self._keep_internals:
			return True
		return record.levelname not in _DEFW_INTERNAL_CATEGORIES


def _install_log_handler(logger_provider, level, keep_internals=False):
	"""
	Put the SDK's handler on the root logger at the given level.

	The root logger's own level still applies first: a record below it never
	reaches any handler, this one included. The handler only narrows further.
	"""
	# The SDK marks this handler deprecated in favour of the one in
	# opentelemetry-instrumentation-logging, a package QFw does not carry.
	# It still works and needs nothing else; revisit when the logs signal
	# is declared stable.
	import warnings

	with warnings.catch_warnings():
		warnings.simplefilter("ignore", DeprecationWarning)
		from opentelemetry.sdk._logs import LoggingHandler

		handler = LoggingHandler(level=level, logger_provider=logger_provider)
	handler.addFilter(_LogSourceFilter(keep_internals))
	logging.getLogger().addHandler(handler)
	return handler


def _remove_log_handler():
	handler = _STATE.log_handler
	if handler is not None:
		try:
			logging.getLogger().removeHandler(handler)
		except Exception:
			pass
	_STATE.log_handler = None


def _metric_export_interval_ms():
	value = _env(METRIC_EXPORT_INTERVAL_ENV)
	if not value:
		return DEFAULT_METRIC_EXPORT_INTERVAL_MS
	try:
		interval = float(value)
	except ValueError:
		interval = -1.0
	if interval <= 0:
		raise ValueError(
			f"{METRIC_EXPORT_INTERVAL_ENV}={value!r} is not a positive number "
			"of milliseconds")
	return interval


def _build_metric_reader(service_name, profile):
	from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

	if profile == PROFILE_OTLP:
		from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
			OTLPMetricExporter)

		exporter = OTLPMetricExporter(
			endpoint=_otlp_endpoint("v1/metrics"))
	else:
		from ._otlp_json import OtlpJsonFileMetricExporter

		stream = _open_export_stream(service_name, "metrics")
		_STATE.streams.append(stream)
		exporter = OtlpJsonFileMetricExporter(stream)
	return PeriodicExportingMetricReader(
		exporter, export_interval_millis=_metric_export_interval_ms())


def _register_defw_trace_hooks():
	"""
	Teach DEFw how to move a trace context across its RPC boundary.

	DEFw carries an opaque carrier and knows nothing about OpenTelemetry, so
	the propagator is supplied from here. Without this, every hop across an
	RPC starts a new unrelated trace and no cross-node breakdown is possible.

	A DEFw build with no seam to register against is not an error. It means
	traces stay per-process until the submodule catches up.
	"""
	try:
		import defw_trace
	except ImportError:
		return False

	from opentelemetry import context as otel_context
	from opentelemetry.propagate import extract, inject

	defw_trace.set_hooks(
		inject=inject,
		attach=lambda carrier: otel_context.attach(extract(carrier)),
		detach=otel_context.detach)
	return True


def _clear_defw_trace_hooks():
	try:
		import defw_trace
	except ImportError:
		return
	defw_trace.clear_hooks()


def configure(service_name, service_version=None, role=None, attributes=None):
	"""
	Bring telemetry up for this process. Safe to call more than once and safe
	to call from more than one thread. Returns the active profile.

	attributes are extra resource attributes, per-process facts such as the
	device a QPM serves, recorded once on the resource rather than on every
	span. Keep them dimensional: a resource attribute may end up a metric
	label downstream.

	Configure once per process, at startup. OpenTelemetry refuses to replace
	a global provider that is already set, so calling shutdown() and then
	configure() again does not rebuild a working provider. shutdown() is for
	flushing on the way out, not for cycling telemetry back up.

	Callers do not need to check the profile first. When telemetry is off, or
	when the SDK is missing, this records that fact and every accessor below
	returns a no-op.
	"""
	with _LOCK:
		if _STATE.configured:
			return _STATE.profile

		_STATE.transport_spans = _env(
			TELEMETRY_TRANSPORT_ENV, "0").lower() in ("1", "yes", "true", "on")

		profile = _profile()
		if profile != PROFILE_OFF and not OTEL_AVAILABLE:
			# Telemetry is never required, so this is not fatal. It is not
			# silent either: an operator who asked for a profile would
			# otherwise get an empty run with no explanation.
			logging.getLogger(__name__).warning(
				"%s=%s was requested but the OpenTelemetry SDK is not "
				"installed, so telemetry stays off. Install "
				"opentelemetry-sdk to enable it.", TELEMETRY_ENV, profile)
			profile = PROFILE_OFF
		if profile == PROFILE_OFF:
			_STATE.profile = PROFILE_OFF
			_STATE.configured = True
			# A transport span cannot be enabled without a provider to
			# receive it. Clearing this keeps guarded call sites at the
			# cost of a boolean test.
			_STATE.transport_spans = False
			return _STATE.profile

		from opentelemetry.sdk.trace import TracerProvider
		from opentelemetry.sdk.metrics import MeterProvider

		resource = _build_resource(
			service_name, service_version, role, attributes)

		tracer_provider = TracerProvider(
			resource=resource, sampler=_build_sampler())
		if profile == PROFILE_OTLP:
			tracer_provider.add_span_processor(_otlp_span_processor())
		else:
			tracer_provider.add_span_processor(
				_file_span_processor(service_name))
		_trace.set_tracer_provider(tracer_provider)

		meter_provider = MeterProvider(
			resource=resource,
			metric_readers=[_build_metric_reader(service_name, profile)])
		_metrics.set_meter_provider(meter_provider)

		logs_level = _logs_level()
		if logs_level is not None:
			from opentelemetry.sdk._logs import LoggerProvider

			logger_provider = LoggerProvider(resource=resource)
			logger_provider.add_log_record_processor(
				_otlp_log_processor() if profile == PROFILE_OTLP
				else _file_log_processor(service_name))
			_STATE.logger_provider = logger_provider
			_STATE.logs_level = logs_level
			_STATE.log_handler = _install_log_handler(
				logger_provider, logs_level, _logs_keep_internals())

		_STATE.profile = profile
		_STATE.tracer_provider = tracer_provider
		_STATE.meter_provider = meter_provider
		_STATE.tracer = _trace.get_tracer("qfw", str(CONVENTIONS_VERSION))
		_STATE.meter = _metrics.get_meter("qfw", str(CONVENTIONS_VERSION))
		_STATE.defw_hooks = _register_defw_trace_hooks()
		if not _STATE.defw_hooks:
			logging.getLogger(__name__).info(
				"DEFw has no trace-context seam, so traces will not stitch "
				"across RPC boundaries. Update the DEFw submodule to get "
				"cross-node traces.")
		_STATE.configured = True
		return _STATE.profile


def enabled():
	"""
	True when a real provider is installed, whatever the sampler then does.

	Use this to skip a hot call site entirely when telemetry is off. A
	skipped call site costs a boolean test, where a sampled-out span still
	pays for the context manager, which is roughly two orders of magnitude
	more.

	Do NOT use this to guard expensive attribute values. Telemetry can be on
	while traces are sampled off, in which case this is True and the span is
	still not recording. Guard attributes with span.is_recording() instead.
	"""
	return _STATE.profile != PROFILE_OFF


def transport_spans_enabled():
	"""
	True when qfw.transport.rpc spans should be emitted.

	Separate from enabled() on purpose. Transport spans sit on DEFw's RPC
	path, where a round-trip can itself be tens of microseconds, so they are
	flag-guarded rather than sampled out and stay off unless the transport is
	the subject of the run.
	"""
	return _STATE.transport_spans


def logs_enabled():
	"""
	True when the logs tier is exporting the root logger's records.

	DEFw's set_logging_level_helper removes every handler from the root
	logger whenever a process sets or changes its DEFw log level, which a
	service does after QFw has configured telemetry. So this does not only
	answer; it puts the tier's handler back if it has gone, and QFw's own
	call sites ask before they write a line.
	"""
	handler = _STATE.log_handler
	if handler is None:
		return False
	root = logging.getLogger()
	if handler not in root.handlers:
		root.addHandler(handler)
	return True


def tracer():
	"""The QFw tracer, or a no-op tracer when telemetry is off."""
	return _STATE.tracer if _STATE.tracer is not None else _NOOP_TRACER


def meter():
	"""The QFw meter, or a no-op meter when telemetry is off."""
	return _STATE.meter if _STATE.meter is not None else _NOOP_METER


def duration_histogram(name):
	"""
	Return a cached duration histogram in seconds.

	Metrics are the always-on tier. They stay recording when traces are
	sampled off, which is why per-hop attribution is carried by histograms
	rather than by spans alone.
	"""
	if _STATE.meter is None:
		return _NOOP_INSTRUMENT
	with _LOCK:
		instrument = _STATE.histograms.get(name)
		if instrument is None:
			description = f"{name} duration in seconds"
			try:
				instrument = _STATE.meter.create_histogram(
					name, unit="s", description=description,
					explicit_bucket_boundaries_advisory=list(DURATION_BUCKETS))
			except TypeError:
				# An API older than 1.23 has no advisory parameter and keeps
				# the SDK's default boundaries.
				instrument = _STATE.meter.create_histogram(
					name, unit="s", description=description)
			_STATE.histograms[name] = instrument
		return instrument


def counter(name):
	"""
	Return a cached monotonic counter.

	Counters carry throughput and reliability, the "how many, and how many
	failed" that a latency histogram alone cannot answer.
	"""
	if _STATE.meter is None:
		return _NOOP_INSTRUMENT
	with _LOCK:
		instrument = _STATE.counters.get(name)
		if instrument is None:
			instrument = _STATE.meter.create_counter(
				name, unit="1", description=f"{name} count")
			_STATE.counters[name] = instrument
		return instrument


def use_providers(tracer_provider, meter_provider=None, profile=PROFILE_FILE,
		  logger_provider=None, logs_level=None, logs_internals=False):
	"""
	Adopt providers built elsewhere instead of building them from the
	environment, and register the DEFw propagation hooks for them.

	For a host process that already owns an OpenTelemetry setup, and for
	tests, which need a fresh recording provider per test where configure()
	can install only one per process. The global providers are left alone,
	so this neither conflicts with an earlier configure() nor requires the
	global slot to be free. shutdown() then shuts these providers down like
	any other.
	"""
	with _LOCK:
		_STATE.transport_spans = _env(
			TELEMETRY_TRANSPORT_ENV, "0").lower() in ("1", "yes", "true", "on")
		_STATE.profile = profile
		_STATE.tracer_provider = tracer_provider
		_STATE.meter_provider = meter_provider
		_STATE.tracer = tracer_provider.get_tracer(
			"qfw", str(CONVENTIONS_VERSION))
		_STATE.meter = (
			meter_provider.get_meter("qfw", str(CONVENTIONS_VERSION))
			if meter_provider is not None else None)
		_STATE.histograms = {}
		_STATE.counters = {}
		_remove_log_handler()
		_STATE.logger_provider = logger_provider
		_STATE.logs_level = (
			None if logger_provider is None
			else logs_level if logs_level is not None else logging.INFO)
		if logger_provider is not None:
			_STATE.log_handler = _install_log_handler(
				logger_provider, _STATE.logs_level, logs_internals)
		_STATE.defw_hooks = _register_defw_trace_hooks()
		_STATE.configured = True
		return _STATE.profile


def shutdown():
	"""
	Flush and close providers. Call on clean service shutdown so batched
	spans are not lost. Safe to call when telemetry was never configured.
	"""
	with _LOCK:
		if _STATE.defw_hooks:
			_clear_defw_trace_hooks()
			_STATE.defw_hooks = False
		if _STATE.tracer_provider is not None:
			try:
				_STATE.tracer_provider.shutdown()
			except Exception:
				pass
		if _STATE.meter_provider is not None:
			try:
				_STATE.meter_provider.shutdown()
			except Exception:
				pass
		_remove_log_handler()
		if _STATE.logger_provider is not None:
			try:
				_STATE.logger_provider.shutdown()
			except Exception:
				pass
		_STATE.logger_provider = None
		_STATE.logs_level = None
		for stream in _STATE.streams:
			try:
				stream.close()
			except Exception:
				pass
		_STATE.streams = []
		_STATE.histograms = {}
		_STATE.counters = {}
		_STATE.tracer = None
		_STATE.meter = None
		_STATE.tracer_provider = None
		_STATE.meter_provider = None
		_STATE.profile = PROFILE_OFF
		_STATE.transport_spans = False
		_STATE.configured = False
