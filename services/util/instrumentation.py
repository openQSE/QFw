"""
QFw's OpenTelemetry conventions, applied at the job path's call sites.

docs/design/benchmarking.md names the spans and metrics, and qfw_telemetry
owns the providers. This module sits between them. It knows what a QFw circuit
is, which hop a call site stands for, and which attributes belong on a span
and which may also be a metric label. Call sites use these helpers and nothing
from OpenTelemetry directly, so with telemetry off every helper costs a boolean
test, and QFw runs unchanged with no OpenTelemetry package installed at all.

One job's trace:

	qfw.app.job                   the Qiskit client, submit() to result()
	  qfw.app.prepare             encoding one circuit for the QPM
	  qfw.qpm.receive             the QPM's async_run or sync_run handler
	    qfw.qpm.queue             circuit created to resources consumed
	    qfw.qpm.dispatch          resources consumed to the provider launch
	    qfw.backend.execute       the run queue's provider call
	      qfw.qpm.transpile       QFw-side transcoding inside the driver
	      qfw.backend.acquire     client, session or resource acquisition
	      qfw.backend.submit      the submission call
	      qfw.backend.collect     polling and result retrieval

qfw.qpm.receive is a child of qfw.app.job because DEFw carries the client's
trace context in its RPC envelope (qfw_telemetry registers the propagator,
DEFw's defw_trace scopes it around the dispatch). Everything under receive
happens after that RPC has returned, in whichever thread dispatches and runs
the circuit, so the receive handler stores its context on the circuit
(bind_circuit) and the later hops parent to that, not to whatever happens to
be current in their thread.

qfw.qpm.queue and qfw.qpm.dispatch are written after the fact from the
timestamps the circuit already keeps (creation_time, resources_consumed_time
and the launch), with explicit start and end times. A span opened early and
ended later would leak whenever a circuit ends some other way, and these are
the same timestamps the client's statistics already read.

Metrics are the always-on tier. Every hop records its duration in the
histogram the design names, with dimensional labels only, so a dashboard has
per-hop numbers while traces are sampled off.
"""
import contextlib
import contextvars
import logging
import time

try:
	import qfw_telemetry as _telemetry
except Exception:  # the package is optional, QFw runs without it
	_telemetry = None

# Span names.
SPAN_APP_JOB = "qfw.app.job"
SPAN_APP_PREPARE = "qfw.app.prepare"
SPAN_QPM_RECEIVE = "qfw.qpm.receive"
SPAN_QPM_TRANSPILE = "qfw.qpm.transpile"
SPAN_QPM_QUEUE = "qfw.qpm.queue"
SPAN_QPM_DISPATCH = "qfw.qpm.dispatch"
SPAN_BACKEND_EXECUTE = "qfw.backend.execute"
# The transport extension, flag-guarded: see the end of this module.
SPAN_TRANSPORT_RPC = "qfw.transport.rpc"
SPAN_TRANSPORT_RETURN = "qfw.transport.return"

# Metric names. Each mirrors the span it aggregates.
METRIC_APP_JOB_DURATION = "qfw.app.job.duration"
METRIC_APP_JOB_COUNT = "qfw.app.job.count"
METRIC_QPM_DURATION = "qfw.qpm.duration"
METRIC_BACKEND_DURATION = "qfw.backend.duration"
METRIC_TRANSPORT_DURATION = "qfw.transport.duration"

# Dimensional attributes: bounded value sets, allowed as metric labels.
ATTR_API_PATH = "qfw.stack.api_path"
ATTR_DEVICE = "qfw.device.name"
ATTR_BACKEND_KIND = "qfw.backend.kind"
ATTR_QPM_OP = "qfw.qpm.op"
ATTR_QPM_REQUEST = "qfw.qpm.request"
ATTR_BACKEND_OP = "qfw.backend.op"
ATTR_OUTCOME = "qfw.outcome"
ATTR_TRANSPORT_OP = "qfw.transport.op"
ATTR_NUM_QUBITS = "qfw.circuit.num_qubits"

# Descriptive attributes: per-job values, on spans only, never metric labels.
ATTR_JOB_ID = "qfw.job.id"
ATTR_CID = "qfw.qpm.cid"
ATTR_QTASK_ID = "qfw.qpm.qtask_id"
ATTR_RESERVATION_ID = "qfw.reservation.id"
ATTR_CIRCUITS = "qfw.app.circuits"
ATTR_SHOTS = "qfw.circuit.shots"
ATTR_CIRCUIT_FORMAT = "qfw.circuit.format"
ATTR_PAYLOAD_BYTES = "qfw.circuit.payload_bytes"
ATTR_VENDOR_JOB_ID = "qfw.vendor.job_id"
ATTR_VENDOR_QUEUE_POSITION = "qfw.vendor.queue_position"
ATTR_POLL_COUNT = "qfw.backend.poll_count"
ATTR_POLL_INTERVAL = "qfw.backend.poll_interval_s"

API_PATH_NATIVE = "native"
API_PATH_QRMI = "qrmi"
API_PATH_QDMI = "qdmi"
API_PATH_SIMULATOR = "simulator"

OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"

TRANSPORT_OP_SUBMIT = "submit"
TRANSPORT_OP_EVENT = "event"
TRANSPORT_OP_RETURN = "return"

_SERVICE_NAMES = {"qpm": "qfw-qpm", "client": "qfw-client"}
_LOG = logging.getLogger(__name__)

# The labels of the qfw.backend.execute in progress on this thread, so the
# phases a driver reports carry the same api path and device on their
# metrics without every driver passing them along.
_EXECUTION_LABELS = contextvars.ContextVar(
	"qfw_execution_labels", default=None)

# A job's story in a few log lines, for the logs tier. Each line is written
# while the job's span is current, so the tier stamps it with the trace and
# the logs panel under a waterfall has something to show for every backend,
# the fake IQM included. Debug, like the rest of what QFw logs about a job,
# and only when the tier is on: with it off these cost a boolean test.
_QPM_LOG = logging.getLogger("qfw.qpm")
_CLIENT_LOG = logging.getLogger("qfw.client")


def _story(span, logger, message, *args):
	logs_enabled = getattr(_telemetry, "logs_enabled", None)
	if logs_enabled is None or not logs_enabled():
		return
	try:
		with _trace_api().use_span(
				span, end_on_exit=False, record_exception=False,
				set_status_on_exception=False):
			logger.debug(message, *args)
	except Exception as exc:
		_LOG.debug("story line not written: %s", exc)


def configure_process(role, device=None, attributes=None):
	"""
	Bring telemetry up for this process, once, under QFw's naming.

	role is the qfw.component.role resource attribute: "qpm" for a QPM service
	process (which also hosts its run queue), "client" for a process using the
	Qiskit backend. device names the device a QPM serves, which lands on the
	resource so a dashboard can group every signal of that service by it.

	Configuration comes from the QFW_TELEMETRY environment, see qfw_telemetry.
	With no profile set this records that telemetry is off and returns.
	"""
	if _telemetry is None:
		return "off"
	extra = dict(attributes or {})
	if device:
		extra.setdefault(ATTR_DEVICE, str(device))
	return _telemetry.configure(
		_SERVICE_NAMES.get(role, f"qfw-{role}"), role=role, attributes=extra)


def enabled():
	"""True when a provider is installed. Says nothing about sampling."""
	return _telemetry is not None and _telemetry.enabled()


def shutdown_process():
	"""
	Flush and close this process's telemetry on a clean stop.

	Batched spans and the periodic metric export otherwise die with the
	process. A service that is killed rather than stopped loses whatever had
	not been exported yet, which is why the export interval is short.
	"""
	if _telemetry is None:
		return
	try:
		_telemetry.shutdown()
	except Exception as exc:
		_LOG.debug("telemetry shutdown failed: %s", exc)


def _trace_api():
	from opentelemetry import trace
	return trace


def _ns(seconds):
	return int(seconds * 1_000_000_000)


def _text(value):
	return None if value is None else str(value)


def _int(value):
	try:
		return None if value is None else int(value)
	except (TypeError, ValueError):
		return None


def _set(span, attributes):
	"""Set attributes on a span that records, dropping None values."""
	if span is None or not span.is_recording():
		return
	for key, value in attributes.items():
		if value is not None:
			span.set_attribute(key, value)


def _mark_error(span, error):
	if span is None or not span.is_recording():
		return
	from opentelemetry.trace import Status, StatusCode
	span.set_status(Status(StatusCode.ERROR, str(error)))
	if isinstance(error, Exception):
		span.record_exception(error)


def _record(name, seconds, labels):
	try:
		_telemetry.duration_histogram(name).record(seconds, labels)
	except Exception as exc:
		_LOG.debug("histogram %s not recorded: %s", name, exc)


def _count(name, labels):
	try:
		_telemetry.counter(name).add(1, labels)
	except Exception as exc:
		_LOG.debug("counter %s not recorded: %s", name, exc)


def set_attribute(key, value):
	"""Set one attribute on the current span, if one records."""
	if value is None or not enabled():
		return
	try:
		_set(_trace_api().get_current_span(), {key: value})
	except Exception as exc:
		_LOG.debug("attribute %s not set: %s", key, exc)


def add_event(name, attributes=None):
	"""Add an event to the current span, if one records."""
	if not enabled():
		return
	try:
		span = _trace_api().get_current_span()
		if span.is_recording():
			span.add_event(name, attributes or {})
	except Exception as exc:
		_LOG.debug("event %s not added: %s", name, exc)


# --- the QPM side ---------------------------------------------------------

def bind_circuit(circuit, qtask_id=None):
	"""
	Remember the receiving request's trace context on the circuit.

	Called while qfw.qpm.receive is current. The hops that follow run after
	the RPC has returned, in a dispatch or run-queue thread, and parent to
	this context so one circuit reads as one subtree of the job's trace.
	"""
	if not enabled():
		return
	try:
		from opentelemetry import context
		circuit.otel_context = context.get_current()
		info = getattr(circuit, "info", None) or {}
		_story(
			_trace_api().get_current_span(), _QPM_LOG,
			"received circuit %s (qtask %s): %s qubits, %s shots",
			circuit.get_cid(), _text(info.get("qtask_id")),
			_int(info.get("num_qubits")),
			_int(info.get("num_shots", info.get("shots"))))
		_set(_trace_api().get_current_span(), {
			ATTR_CID: circuit.get_cid(),
			ATTR_QTASK_ID: _text(qtask_id),
			ATTR_RESERVATION_ID: _text(info.get("reservation_id")),
		})
	except Exception as exc:
		_LOG.debug("trace context not bound to the circuit: %s", exc)


@contextlib.contextmanager
def qpm_receive(request="async_run"):
	"""
	qfw.qpm.receive around a QPM execution request handler.

	Its duration is the QPM's own time in the RPC: parsing, admission, the
	scheduler and, when a slot is free, the hand-off to the provider. For
	sync_run it also spans the wait for the result, which is why the request
	kind is a label on the histogram.
	"""
	if not enabled():
		yield None
		return
	labels = {ATTR_QPM_OP: "receive", ATTR_QPM_REQUEST: request}
	started = time.monotonic()
	outcome = OUTCOME_COMPLETED
	with _telemetry.tracer().start_as_current_span(
			SPAN_QPM_RECEIVE, record_exception=False,
			set_status_on_exception=False) as span:
		_set(span, labels)
		try:
			yield span
		except BaseException as error:
			outcome = OUTCOME_FAILED
			_mark_error(span, error)
			raise
		finally:
			_set(span, {ATTR_OUTCOME: outcome})
			_record(METRIC_QPM_DURATION, time.monotonic() - started, labels)


def _window(start_s, end_s):
	try:
		return (start_s is not None and end_s is not None
			and start_s > 0 and end_s >= start_s)
	except TypeError:
		return False


def record_qpm_phase(circuit, op, start_s, end_s, attributes=None):
	"""
	Write a qfw.qpm.<op> span for a phase that has already happened, from two
	wall-clock timestamps, and record its duration.

	The metric is recorded whatever the circuit carries. The span needs the
	context bind_circuit stored, because a root span for one hop of one
	circuit would be noise rather than a trace.
	"""
	if not enabled() or not _window(start_s, end_s):
		return
	_record(METRIC_QPM_DURATION, end_s - start_s, {ATTR_QPM_OP: op})
	parent = getattr(circuit, "otel_context", None)
	if parent is None:
		return
	try:
		span = _telemetry.tracer().start_span(
			f"qfw.qpm.{op}", context=parent, start_time=_ns(start_s))
		info = getattr(circuit, "info", None) or {}
		values = {
			ATTR_QPM_OP: op,
			ATTR_CID: circuit.get_cid(),
			ATTR_QTASK_ID: _text(info.get("qtask_id")),
		}
		values.update(attributes or {})
		_set(span, values)
		span.end(end_time=_ns(end_s))
	except Exception as exc:
		_LOG.debug("qfw.qpm.%s not recorded: %s", op, exc)


def record_queue(circuit):
	"""qfw.qpm.queue: from the circuit's creation to its resources being taken."""
	record_qpm_phase(
		circuit, "queue",
		getattr(circuit, "creation_time", None),
		getattr(circuit, "resources_consumed_time", None))


# --- the run queue and the drivers ----------------------------------------

class _Execution(object):
	__slots__ = ("span", "labels", "started")

	def __init__(self, span, labels, started):
		self.span = span
		self.labels = labels
		self.started = started


def _execution_labels(api_path, device, backend_kind):
	labels = {ATTR_API_PATH: str(api_path)}
	if device:
		labels[ATTR_DEVICE] = str(device)
	if backend_kind:
		labels[ATTR_BACKEND_KIND] = str(backend_kind)
	return labels


def begin_backend_execution(circuit, api_path, device=None, backend_kind=None):
	"""
	Open qfw.backend.execute for a circuit the run queue is about to run.

	Closes the hand-off as well: qfw.qpm.dispatch runs from the moment the
	circuit's resources were taken to now. Returns a handle for
	finish_backend_execution, or None when telemetry is off. The span is not
	made current; backend_execution does that for drivers that run inline.
	"""
	if not enabled():
		return None
	labels = _execution_labels(api_path, device, backend_kind)
	record_qpm_phase(
		circuit, "dispatch",
		getattr(circuit, "resources_consumed_time", None), time.time())
	try:
		span = _telemetry.tracer().start_span(
			SPAN_BACKEND_EXECUTE,
			context=getattr(circuit, "otel_context", None))
		info = getattr(circuit, "info", None) or {}
		values = dict(labels)
		values.update({
			ATTR_BACKEND_OP: "execute",
			ATTR_CID: circuit.get_cid(),
			ATTR_QTASK_ID: _text(info.get("qtask_id")),
			ATTR_SHOTS: _int(info.get("num_shots", info.get("shots"))),
			ATTR_NUM_QUBITS: _int(info.get("num_qubits")),
		})
		_set(span, values)
		_story(
			span, _QPM_LOG, "executing circuit %s on %s via %s",
			circuit.get_cid(), device or "?", api_path)
		return _Execution(span, labels, time.monotonic())
	except Exception as exc:
		_LOG.debug("qfw.backend.execute not opened: %s", exc)
		return None


def _outcome(circuit, error, cancel_event):
	for event in (cancel_event, getattr(circuit, "cancel_event", None)):
		if event is not None and event.is_set():
			return OUTCOME_CANCELLED
	if error is not None:
		return OUTCOME_FAILED
	state = getattr(circuit, "getState", None)
	if state is not None:
		try:
			from util.qpm.util_circuit import CircuitStates
			if state() == CircuitStates.FAIL:
				return OUTCOME_FAILED
		except Exception:
			pass
	return OUTCOME_COMPLETED


def finish_backend_execution(execution, circuit, error=None, cancel_event=None):
	"""Close a qfw.backend.execute opened by begin_backend_execution."""
	if execution is None:
		return
	outcome = _outcome(circuit, error, cancel_event)
	try:
		if error is not None:
			_mark_error(execution.span, error)
		_set(execution.span, {ATTR_OUTCOME: outcome})
		_story(
			execution.span, _QPM_LOG, "circuit %s %s on %s after %.1f ms",
			circuit.get_cid(), outcome,
			execution.labels.get(ATTR_DEVICE, "?"),
			(time.monotonic() - execution.started) * 1000.0)
		execution.span.end()
	except Exception as exc:
		_LOG.debug("qfw.backend.execute not closed: %s", exc)
	labels = dict(execution.labels)
	labels[ATTR_BACKEND_OP] = "execute"
	labels[ATTR_OUTCOME] = outcome
	_record(METRIC_BACKEND_DURATION, time.monotonic() - execution.started, labels)


@contextlib.contextmanager
def backend_execution(circuit, api_path, device=None, backend_kind=None,
		      cancel_event=None):
	"""
	qfw.backend.execute around a run queue's inline provider call.

	The span is current inside the block, so the phases the driver reports
	(backend_phase, qpm_transpile) nest under it and carry its labels. The
	outcome is cancelled when a cancel event is set, failed when the block
	raised or left the circuit in its failed state, and completed otherwise.
	"""
	execution = begin_backend_execution(circuit, api_path, device, backend_kind)
	if execution is None:
		yield None
		return
	token = _EXECUTION_LABELS.set(execution.labels)
	error = None
	try:
		with _trace_api().use_span(
				execution.span, end_on_exit=False, record_exception=False,
				set_status_on_exception=False):
			yield execution.span
	except BaseException as exc:
		error = exc
		raise
	finally:
		_EXECUTION_LABELS.reset(token)
		finish_backend_execution(
			execution, circuit, error=error, cancel_event=cancel_event)


@contextlib.contextmanager
def _phase(span_name, metric, labels):
	if not enabled():
		yield None
		return
	started = time.monotonic()
	outcome = OUTCOME_COMPLETED
	with _telemetry.tracer().start_as_current_span(
			span_name, record_exception=False,
			set_status_on_exception=False) as span:
		_set(span, labels)
		try:
			yield span
		except BaseException as error:
			outcome = OUTCOME_FAILED
			_mark_error(span, error)
			raise
		finally:
			_set(span, {ATTR_OUTCOME: outcome})
			values = dict(labels)
			values[ATTR_OUTCOME] = outcome
			_record(metric, time.monotonic() - started, values)


def backend_phase(op):
	"""
	qfw.backend.<op> for one phase of a driver's provider interaction:
	acquire, submit or collect. Use inside backend_execution, whose api path
	and device the phase's metric then carries.
	"""
	labels = dict(_EXECUTION_LABELS.get() or {})
	labels[ATTR_BACKEND_OP] = op
	return _phase(f"qfw.backend.{op}", METRIC_BACKEND_DURATION, labels)


def qpm_transpile():
	"""
	qfw.qpm.transpile around QFw-side transcoding of the circuit into the
	provider's program, which the shim and native drivers do on the way in.
	"""
	return _phase(
		SPAN_QPM_TRANSPILE, METRIC_QPM_DURATION, {ATTR_QPM_OP: "transpile"})


# --- the Qiskit client ----------------------------------------------------

class _JobTrace(object):
	__slots__ = ("span", "labels", "started", "ended")

	def __init__(self, span, labels, started):
		self.span = span
		self.labels = labels
		self.started = started
		self.ended = False


def job_labels(properties):
	"""The dimensional labels a job carries, from what its QPM published."""
	properties = properties or {}
	labels = {}
	device = properties.get("device_id")
	kind = properties.get("device_provider") or properties.get("provider")
	if device:
		labels[ATTR_DEVICE] = str(device)
	if kind:
		labels[ATTR_BACKEND_KIND] = str(kind)
	return labels


def start_job(job_id, circuits, shots, properties=None):
	"""
	Open qfw.app.job, the root of the job's trace. Returns a handle for
	job_scope and end_job, or None when telemetry is off.

	The span is not ended by a context manager because submit() and result()
	are separate calls from the application, and the job is whatever happens
	between them.
	"""
	if not enabled():
		return None
	try:
		labels = job_labels(properties)
		span = _telemetry.tracer().start_span(SPAN_APP_JOB)
		values = dict(labels)
		values.update({
			ATTR_JOB_ID: str(job_id),
			ATTR_CIRCUITS: _int(circuits),
			ATTR_SHOTS: _int(shots),
		})
		_set(span, values)
		_story(
			span, _CLIENT_LOG, "submitted job %s: %s circuit(s), %s shots",
			job_id, _int(circuits), _int(shots))
		return _JobTrace(span, labels, time.monotonic())
	except Exception as exc:
		_LOG.debug("qfw.app.job not opened: %s", exc)
		return None


@contextlib.contextmanager
def job_scope(job):
	"""
	Make the job's span current for a block, so the RPCs made inside it carry
	the job's trace context to the QPM and the prepare spans nest under it.
	"""
	if job is None:
		yield
		return
	with _trace_api().use_span(
			job.span, end_on_exit=False, record_exception=False,
			set_status_on_exception=False):
		yield


def end_job(job, outcome, error=None):
	"""Close qfw.app.job and record the job's duration and count."""
	if job is None or job.ended:
		return
	job.ended = True
	try:
		if error is not None:
			_mark_error(job.span, error)
		_set(job.span, {ATTR_OUTCOME: outcome})
		_story(
			job.span, _CLIENT_LOG, "job %s %s after %.1f ms",
			job.span.attributes.get(ATTR_JOB_ID) if getattr(job.span, "attributes", None) else "?",
			outcome, (time.monotonic() - job.started) * 1000.0)
		job.span.end()
	except Exception as exc:
		_LOG.debug("qfw.app.job not closed: %s", exc)
	labels = dict(job.labels)
	labels[ATTR_OUTCOME] = outcome
	_record(METRIC_APP_JOB_DURATION, time.monotonic() - job.started, labels)
	_count(METRIC_APP_JOB_COUNT, labels)


@contextlib.contextmanager
def app_prepare(circuit):
	"""qfw.app.prepare around encoding one circuit for the QPM."""
	if not enabled():
		yield None
		return
	with _telemetry.tracer().start_as_current_span(
			SPAN_APP_PREPARE, record_exception=False,
			set_status_on_exception=False) as span:
		_set(span, {ATTR_NUM_QUBITS: _int(getattr(circuit, "num_qubits", None))})
		try:
			yield span
		except BaseException as error:
			_mark_error(span, error)
			raise


def describe_payload(span, info):
	"""Record the encoded circuit's format and size on the prepare span."""
	if span is None or not span.is_recording():
		return
	try:
		circuit = info.get("circuit")
		if isinstance(circuit, dict):
			fmt = circuit.get("format")
			data = circuit.get("data")
		else:
			fmt = "openqasm2"
			data = info.get("qasm")
		size = len(data) if isinstance(data, (str, bytes)) else None
		_set(span, {ATTR_CIRCUIT_FORMAT: fmt, ATTR_PAYLOAD_BYTES: size})
	except Exception as exc:
		_LOG.debug("payload not described: %s", exc)


# --- the transport extension ---------------------------------------------
#
# The job-path spans above measure what the two processes do. What they
# leave out is the transport between them, and on a fast job that is most
# of the client's time: the run RPC on the way in, the completion event on
# the way back. These call sites close that gap. They are the design's
# optional transport extension and are flag-guarded by
# QFW_TELEMETRY_TRANSPORT rather than sampled, because a sampled-out span
# still runs its call site.

def transport_enabled():
	"""True when the transport call sites should run at all."""
	return enabled() and _telemetry.transport_spans_enabled()


@contextlib.contextmanager
def transport_rpc(op, context=None, labels=None):
	"""
	qfw.transport.rpc around one RPC the job path makes: op "submit" for
	the client's run request, "event" for the QPM's completion-event push.
	The span joins the current context, or the one given, so a push made
	from a run-queue thread still lands in the job's trace. A client passes
	its job's dimensional labels, so its series carry the device like the
	QPM's do from its resource.
	"""
	if not transport_enabled():
		yield None
		return
	labels = dict(labels or {})
	labels[ATTR_TRANSPORT_OP] = op
	started = time.monotonic()
	outcome = OUTCOME_COMPLETED
	with _telemetry.tracer().start_as_current_span(
			SPAN_TRANSPORT_RPC, context=context, record_exception=False,
			set_status_on_exception=False) as span:
		_set(span, labels)
		try:
			yield span
		except BaseException as error:
			outcome = OUTCOME_FAILED
			_mark_error(span, error)
			raise
		finally:
			_set(span, {ATTR_OUTCOME: outcome})
			values = dict(labels)
			values[ATTR_OUTCOME] = outcome
			_record(METRIC_TRANSPORT_DURATION, time.monotonic() - started,
				values)


def record_transport_return(result, received_s=None, labels=None):
	"""
	qfw.transport.return, written after the fact on the client: from the
	provider's completion of a circuit, the completion_time its result
	carries, to the moment the client picked the completion event up. That
	is the whole way back: the QRC's result assembly, the QPM's publish, the
	event RPC and the client's wake-up.

	The two ends are read on two clocks, so the window is skipped unless it
	is non-negative. On one host the clocks agree; across hosts the number
	is only as good as their synchronisation.
	"""
	if not transport_enabled() or not isinstance(result, dict):
		return
	completed_s = (
		result.get("completion_time") or result.get("cq_enqueue_time"))
	received_s = time.time() if received_s is None else received_s
	if not _window(completed_s, received_s):
		return
	values = dict(labels or {})
	values[ATTR_TRANSPORT_OP] = TRANSPORT_OP_RETURN
	_record(METRIC_TRANSPORT_DURATION, received_s - completed_s, values)
	try:
		span = _telemetry.tracer().start_span(
			SPAN_TRANSPORT_RETURN, start_time=_ns(completed_s))
		_set(span, {
			ATTR_TRANSPORT_OP: TRANSPORT_OP_RETURN,
			ATTR_CID: _text(result.get("cid")),
			ATTR_QTASK_ID: _text(result.get("qtask_id")),
		})
		span.end(end_time=_ns(received_s))
	except Exception as exc:
		_LOG.debug("%s not recorded: %s", SPAN_TRANSPORT_RETURN, exc)
