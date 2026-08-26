"""
QFw telemetry bootstrap.

Owns OpenTelemetry provider setup, the deployment profile, and the sampling
policy described in docs/benchmarking-design.md. This package emits no spans
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
QFW_TELEMETRY_ENDPOINT   collector endpoint for the otlp profile
"""

import logging
import os
import threading

TELEMETRY_ENV = "QFW_TELEMETRY"
TELEMETRY_DIR_ENV = "QFW_TELEMETRY_DIR"
TELEMETRY_SAMPLE_ENV = "QFW_TELEMETRY_SAMPLE"
TELEMETRY_TRANSPORT_ENV = "QFW_TELEMETRY_TRANSPORT"
TELEMETRY_ENDPOINT_ENV = "QFW_TELEMETRY_ENDPOINT"

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
		self.streams = []
		self.transport_spans = False
		self.defw_hooks = False


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


def _build_resource(service_name, service_version, role):
	from opentelemetry.sdk.resources import Resource

	attributes = {
		"service.name": service_name,
		"qfw.conventions.version": CONVENTIONS_VERSION,
	}
	if service_version:
		attributes["service.version"] = service_version
	if role:
		attributes["qfw.component.role"] = role
	slurm_job = _env("SLURM_JOB_ID")
	if slurm_job:
		attributes["qfw.slurm.job_id"] = slurm_job
	return Resource.create(attributes)


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


def _otlp_span_processor():
	from opentelemetry.sdk.trace.export import BatchSpanProcessor
	from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
		OTLPSpanExporter)

	endpoint = _env(TELEMETRY_ENDPOINT_ENV)
	if endpoint:
		exporter = OTLPSpanExporter(endpoint=endpoint)
	else:
		exporter = OTLPSpanExporter()
	return BatchSpanProcessor(exporter)


def _build_metric_reader(service_name, profile):
	from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

	if profile == PROFILE_OTLP:
		from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
			OTLPMetricExporter)

		endpoint = _env(TELEMETRY_ENDPOINT_ENV)
		if endpoint:
			exporter = OTLPMetricExporter(endpoint=endpoint)
		else:
			exporter = OTLPMetricExporter()
	else:
		from ._otlp_json import OtlpJsonFileMetricExporter

		stream = _open_export_stream(service_name, "metrics")
		_STATE.streams.append(stream)
		exporter = OtlpJsonFileMetricExporter(stream)
	return PeriodicExportingMetricReader(exporter)


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


def configure(service_name, service_version=None, role=None):
	"""
	Bring telemetry up for this process. Safe to call more than once and safe
	to call from more than one thread. Returns the active profile.

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

		resource = _build_resource(service_name, service_version, role)

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
			instrument = _STATE.meter.create_histogram(
				name, unit="s", description=f"{name} duration in seconds")
			_STATE.histograms[name] = instrument
		return instrument


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
		for stream in _STATE.streams:
			try:
				stream.close()
			except Exception:
				pass
		_STATE.streams = []
		_STATE.histograms = {}
		_STATE.tracer = None
		_STATE.meter = None
		_STATE.tracer_provider = None
		_STATE.meter_provider = None
		_STATE.profile = PROFILE_OFF
		_STATE.transport_spans = False
		_STATE.configured = False
