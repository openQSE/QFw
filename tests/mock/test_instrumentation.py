"""
The job path's spans and metrics, checked in one process.

A fresh recording tracer and meter are adopted through qfw_telemetry's
use_providers seam for each test, so these tests do not depend on the single
global provider a process may install, and run with QFW_TELEMETRY unset the
way CI does. The QPM side is driven through the fake IQM QPM, whose run queue
is the simplest real one, and the client side through QFwJob with the mock
QPM the job tests already use.
"""
import logging
import time
import types

import pytest

import qfw_telemetry
from svc_fake_iqm_qpm.svc_qpm import FAKE_IQM_TARGET_ID, QPM
from tests.mock.fakes import (
	FakeEventAPI,
	FakeQPM,
	FakeSchedulerContext,
	make_result_event,
)
from tests.mock.test_fake_iqm_qpm import (
	FakeAdmissionContext,
	_setup,
	_wait_for_completion,
	configure_fake_credentials,
)
from tests.mock.test_qfw_job import FakeBackend, _driver_options, _stub_qasm
from util import instrumentation


class _Recording:
	def __init__(self, exporter, reader, log_exporter=None):
		self.exporter = exporter
		self.reader = reader
		self.log_exporter = log_exporter

	def logs(self):
		"""(body, trace_id) of every record the logs tier exported."""
		finished = getattr(
			self.log_exporter, "get_finished_log_records",
			getattr(self.log_exporter, "get_finished_logs", None))()
		return [(r.log_record.body, r.log_record.trace_id) for r in finished]

	def spans_by_name(self):
		groups = {}
		for span in self.exporter.get_finished_spans():
			groups.setdefault(span.name, []).append(span)
		return groups

	def points(self, metric_name):
		"""(attributes, data point) for every point of one metric."""
		found = []
		data = self.reader.get_metrics_data()
		if data is None:
			return found
		for resource_metrics in data.resource_metrics:
			for scope_metrics in resource_metrics.scope_metrics:
				for metric in scope_metrics.metrics:
					if metric.name != metric_name:
						continue
					for point in metric.data.data_points:
						found.append((dict(point.attributes), point))
		return found


@pytest.fixture
def recording():
	"""A recording tracer and meter for one test, adopted by qfw_telemetry."""
	pytest.importorskip("opentelemetry.sdk")
	from opentelemetry.sdk.metrics import MeterProvider
	from opentelemetry.sdk.metrics.export import InMemoryMetricReader
	from opentelemetry.sdk.trace import TracerProvider
	from opentelemetry.sdk.trace.export import SimpleSpanProcessor
	from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
		InMemorySpanExporter)
	from opentelemetry.sdk.trace.sampling import ALWAYS_ON

	from opentelemetry.sdk._logs import LoggerProvider
	from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
	try:
		from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
	except ImportError:  # older SDKs
		from opentelemetry.sdk._logs.export import (
			InMemoryLogExporter as InMemoryLogRecordExporter)

	exporter = InMemorySpanExporter()
	tracer_provider = TracerProvider(sampler=ALWAYS_ON)
	tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))
	reader = InMemoryMetricReader()
	meter_provider = MeterProvider(metric_readers=[reader])
	# The logs tier too, at the level that carries every DEFw line.
	log_exporter = InMemoryLogRecordExporter()
	logger_provider = LoggerProvider()
	logger_provider.add_log_record_processor(
		SimpleLogRecordProcessor(log_exporter))
	qfw_telemetry.use_providers(
		tracer_provider, meter_provider,
		logger_provider=logger_provider, logs_level=logging.DEBUG)
	assert instrumentation.enabled()
	assert qfw_telemetry.logs_enabled()
	# The story lines are debug and the root logger may be quieter than
	# that: the tier opens QFw's own loggers itself, as it must for a
	# real client, so the fixture leaves the root alone.
	yield _Recording(exporter, reader, log_exporter)
	qfw_telemetry.shutdown()
	assert not instrumentation.enabled()


def _fake_iqm_qpm():
	return QPM(
		admission_context_factory=FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext,
	)


def _reserve(qpm, user):
	return qpm.reserve(request={
		"owner": {"user": user},
		"job_id": "job-trace",
		"scope_id": "allocation-1",
		"target_device_id": FAKE_IQM_TARGET_ID,
		"walltime_ns": 1_000_000_000,
		"task_class": {
			"qubit_count": 4,
			"depth": 12,
			"one_q_gate_count": 20,
			"two_q_gate_count": 6,
			"measurement_count": 4,
			"shots": 64,
		},
	})


_CIRCUIT = {
	"qasm": "OPENQASM 2.0;",
	"num_qubits": 4,
	"shots": 64,
	"depth": 12,
	"one_q_gate_count": 20,
	"two_q_gate_count": 6,
	"measurement_count": 4,
}


@pytest.mark.filterwarnings(
	"error::pytest.PytestUnhandledThreadExceptionWarning")
def test_qpm_run_is_one_trace_with_every_hop(monkeypatch, tmp_path, recording):
	_setup(monkeypatch)
	configure_fake_credentials(monkeypatch, tmp_path, "trace-user")
	qpm = _fake_iqm_qpm()
	decision = _reserve(qpm, "trace-user")

	# The caller's span, current the way DEFw's handle_rpc_req leaves the
	# client's context attached around the dispatch.
	with qfw_telemetry.tracer().start_as_current_span("qfw.app.job") as job:
		response = qpm.async_run(
			dict(_CIRCUIT), reservation_id=decision["reservation_id"])
		# A line written while the job's span is current, the way DEFw's
		# own logging does inside the receive handler, carries its trace.
		logging.getLogger("qfw.test").log(33, "job %s submitted", "job-trace")
	completion = _wait_for_completion(
		qpm, response["cid"], decision["reservation_id"])
	assert completion["outcome"] == "COMPLETED"
	assert ("job job-trace submitted", job.get_span_context().trace_id) in \
		recording.logs()
	# The QPM's own story of the circuit, each line stitched to the trace:
	# received while the receive handler ran, then executing and done.
	trace_id = job.get_span_context().trace_id
	story = [body for body, tid in recording.logs() if tid == trace_id
		and body.startswith(("received circuit", "executing circuit", "circuit "))]
	cid = response["cid"]
	assert story[0] == f"received circuit {cid} (qtask 1): 4 qubits, 64 shots"
	assert story[1] == f"executing circuit {cid} on {FAKE_IQM_TARGET_ID} via simulator"
	assert story[2].startswith(f"circuit {cid} completed on {FAKE_IQM_TARGET_ID} after ")
	assert len(story) == 3

	spans = recording.spans_by_name()
	assert set(spans) == {
		"qfw.app.job", "qfw.qpm.receive", "qfw.qpm.queue",
		"qfw.qpm.dispatch", "qfw.backend.execute"}
	for name, group in spans.items():
		assert len(group) == 1, name

	# One trace. The receive handler joins the caller, and every later hop
	# joins the receive handler through the context stored on the circuit,
	# although two of them ran in the run queue's thread.
	trace_ids = {g[0].context.trace_id for g in spans.values()}
	assert trace_ids == {job.get_span_context().trace_id}
	receive = spans["qfw.qpm.receive"][0]
	assert receive.parent.span_id == job.get_span_context().span_id
	for name in ("qfw.qpm.queue", "qfw.qpm.dispatch", "qfw.backend.execute"):
		assert spans[name][0].parent.span_id == receive.context.span_id, name

	execute = spans["qfw.backend.execute"][0]
	assert execute.attributes["qfw.stack.api_path"] == "simulator"
	assert execute.attributes["qfw.device.name"] == FAKE_IQM_TARGET_ID
	assert execute.attributes["qfw.backend.kind"] == "fake-iqm"
	assert execute.attributes["qfw.outcome"] == "completed"
	assert execute.attributes["qfw.qpm.cid"] == response["cid"]
	assert execute.attributes["qfw.circuit.shots"] == 64
	assert receive.attributes["qfw.qpm.cid"] == response["cid"]
	assert receive.attributes["qfw.qpm.request"] == "async_run"
	assert receive.attributes["qfw.reservation.id"] == str(
		decision["reservation_id"])

	# The written-after-the-fact phases carry the circuit's own clocks and
	# tile the time before the provider call, in order.
	queue = spans["qfw.qpm.queue"][0]
	dispatch = spans["qfw.qpm.dispatch"][0]
	assert queue.start_time <= queue.end_time
	assert queue.end_time <= dispatch.start_time + 1  # same clock reading
	assert dispatch.end_time <= execute.start_time
	assert execute.start_time <= execute.end_time

	# The always-on tier: one duration per hop, labelled by op only, and the
	# backend's by api path, device and outcome. Nothing per-job on a label.
	qpm_points = recording.points("qfw.qpm.duration")
	assert {a["qfw.qpm.op"] for a, _ in qpm_points} == {
		"receive", "queue", "dispatch"}
	backend_points = recording.points("qfw.backend.duration")
	assert [a["qfw.backend.op"] for a, _ in backend_points] == ["execute"]
	attributes, point = backend_points[0]
	assert attributes == {
		"qfw.backend.op": "execute",
		"qfw.stack.api_path": "simulator",
		"qfw.device.name": FAKE_IQM_TARGET_ID,
		"qfw.backend.kind": "fake-iqm",
		"qfw.outcome": "completed",
	}
	assert point.count == 1
	# Buckets sized for seconds, not the SDK's millisecond defaults, so a
	# quantile over sub-second hops means something.
	assert list(point.explicit_bounds) == list(qfw_telemetry.DURATION_BUCKETS)
	for attributes, _ in qpm_points + backend_points:
		assert "qfw.qpm.cid" not in attributes
		assert "qfw.reservation.id" not in attributes


@pytest.mark.filterwarnings(
	"error::pytest.PytestUnhandledThreadExceptionWarning")
def test_cancelled_run_reports_a_cancelled_outcome(monkeypatch, tmp_path,
						  recording):
	_setup(monkeypatch)
	monkeypatch.setenv("QFW_FAKE_QPM_MIN_SLEEP_SECONDS", "0.5")
	monkeypatch.setenv("QFW_FAKE_QPM_MAX_SLEEP_SECONDS", "0.5")
	configure_fake_credentials(monkeypatch, tmp_path, "trace-user")
	qpm = _fake_iqm_qpm()
	decision = _reserve(qpm, "trace-user")
	response = qpm.async_run(
		dict(_CIRCUIT), reservation_id=decision["reservation_id"])
	status = qpm.cancel_task(
		cid=response["cid"], reservation_id=decision["reservation_id"])
	assert status["outcome"] in ("FAILED", "CANCELLED", "CANCELLING"), status
	completion = _wait_for_completion(
		qpm, response["cid"], decision["reservation_id"], timeout=3.0)
	assert completion["outcome"] != "COMPLETED"

	# The QPM publishes the cancellation as soon as it has signalled the run
	# queue, and the run queue's thread notices on its next wake-up, so the
	# execution span can end a few milliseconds after the completion shows.
	execute = _wait_for_span(recording, "qfw.backend.execute")
	assert execute.attributes["qfw.outcome"] == "cancelled"
	(attributes, _), = recording.points("qfw.backend.duration")
	assert attributes["qfw.outcome"] == "cancelled"


def _wait_for_span(recording, name, timeout=3.0):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		spans = recording.spans_by_name().get(name)
		if spans:
			return spans[0]
		time.sleep(0.01)
	raise AssertionError(f"span {name} was not exported within {timeout}s")


class _TracingFakeQPM(FakeQPM):
	"""Records the span current at async_run: the context DEFw would carry."""

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.contexts = []

	def async_run(self, info, **kwargs):
		from opentelemetry import trace
		self.contexts.append(trace.get_current_span().get_span_context())
		return super().async_run(info, **kwargs)


def _client_job(monkeypatch, fake_qpm, events):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm.qpm_properties = {
		"device_id": FAKE_IQM_TARGET_ID, "provider": "fake-iqm"}
	circuit = qfw_job.QuantumCircuit(2, name="bell")
	event_api = FakeEventAPI(events=events, fd=42)
	options = _driver_options(shots=3, seed=7, seed_simulator=13)
	monkeypatch.setattr(
		qfw_job.select, "select", lambda r, w, x, t: (r, [], []))
	_stub_qasm(monkeypatch)
	return circuit, qfw_job.QFwJob(
		FakeBackend(), fake_qpm, event_api, circuit, options)


def test_qiskit_job_is_the_trace_root_and_counts_itself(monkeypatch, recording):
	fake_qpm = _TracingFakeQPM(cids=["cid-1"])
	circuit, job = _client_job(
		monkeypatch, fake_qpm,
		[make_result_event("cid-1", {"00": 2, "11": 1})])
	job.submit()
	result = job.result()
	assert result.get_counts(circuit) == {"00": 2, "11": 1}

	spans = recording.spans_by_name()
	assert set(spans) == {"qfw.app.job", "qfw.app.prepare"}
	(root,) = spans["qfw.app.job"]
	(prepare,) = spans["qfw.app.prepare"]
	assert root.parent is None
	assert prepare.parent.span_id == root.context.span_id
	# The RPC to the QPM was made with the job's span current, which is what
	# puts the QPM's spans in this trace on a real deployment.
	assert fake_qpm.contexts == [root.context]

	assert root.attributes["qfw.job.id"] == job._job_id
	# The client's two lines of the story, under the job's trace.
	assert [body for body, tid in recording.logs()
		if tid == root.context.trace_id and body.startswith(("submitted job", "job "))] == [
		f"submitted job {job._job_id}: 1 circuit(s), 3 shots",
		next(body for body, _ in recording.logs()
			if body.startswith(f"job {job._job_id} completed after"))]
	assert root.attributes["qfw.app.circuits"] == 1
	assert root.attributes["qfw.circuit.shots"] == 3
	assert root.attributes["qfw.device.name"] == FAKE_IQM_TARGET_ID
	assert root.attributes["qfw.backend.kind"] == "fake-iqm"
	assert root.attributes["qfw.outcome"] == "completed"
	assert prepare.attributes["qfw.circuit.num_qubits"] == 2
	assert prepare.attributes["qfw.circuit.format"] == "openqasm2"
	assert prepare.attributes["qfw.circuit.payload_bytes"] == len(
		"OPENQASM 2.0;")

	(duration,) = recording.points("qfw.app.job.duration")
	assert duration[0] == {
		"qfw.device.name": FAKE_IQM_TARGET_ID,
		"qfw.backend.kind": "fake-iqm",
		"qfw.outcome": "completed",
	}
	assert duration[1].count == 1
	(count,) = recording.points("qfw.app.job.count")
	assert count[0] == duration[0]
	assert count[1].value == 1
	# The transport extension is off unless asked for: no span, no metric.
	assert recording.points("qfw.transport.duration") == []


def test_failed_submission_closes_the_job_as_failed(monkeypatch, recording):
	from opentelemetry.trace import StatusCode

	fake_qpm = FakeQPM(async_error=RuntimeError("QPM is down"))
	_circuit, job = _client_job(monkeypatch, fake_qpm, [])
	with pytest.raises(RuntimeError):
		job.submit()

	(root,) = recording.spans_by_name()["qfw.app.job"]
	assert root.attributes["qfw.outcome"] == "failed"
	assert root.status.status_code == StatusCode.ERROR
	(count,) = recording.points("qfw.app.job.count")
	assert count[0]["qfw.outcome"] == "failed"
	assert count[1].value == 1


def _transport_on(monkeypatch):
	# The flag is read from the environment when providers are adopted, so
	# the recording fixture has already decided it; flip the state directly.
	monkeypatch.setattr(qfw_telemetry._STATE, "transport_spans", True)


def test_transport_spans_cover_the_way_in_and_the_way_back(monkeypatch, recording):
	_transport_on(monkeypatch)
	fake_qpm = _TracingFakeQPM(cids=["cid-1"])
	completed = time.time() - 0.02
	circuit, job = _client_job(
		monkeypatch, fake_qpm,
		[make_result_event("cid-1", {"00": 2, "11": 1}, offset=completed - 5.0)])
	job.submit()
	assert job.result().get_counts(circuit) == {"00": 2, "11": 1}

	spans = recording.spans_by_name()
	assert set(spans) == {
		"qfw.app.job", "qfw.app.prepare",
		"qfw.transport.rpc", "qfw.transport.return"}
	(root,) = spans["qfw.app.job"]
	(rpc,) = spans["qfw.transport.rpc"]
	(back,) = spans["qfw.transport.return"]

	# The run RPC: inside the job, and current when the RPC was made, so on
	# a real deployment the QPM's receive nests under it.
	assert rpc.parent.span_id == root.context.span_id
	assert rpc.attributes["qfw.transport.op"] == "submit"
	assert rpc.attributes["qfw.outcome"] == "completed"
	assert fake_qpm.contexts == [rpc.context]

	# The way back, written after the fact: from the provider's completion
	# clock to the client's pick-up, as a child of the job.
	assert back.parent.span_id == root.context.span_id
	assert back.attributes["qfw.transport.op"] == "return"
	assert back.attributes["qfw.qpm.cid"] == "cid-1"
	assert back.start_time == int(completed * 1_000_000_000)
	assert back.end_time >= back.start_time + 20_000_000

	points = recording.points("qfw.transport.duration")
	assert {a["qfw.transport.op"] for a, _ in points} == {"submit", "return"}
	back_point = next(p for a, p in points if a["qfw.transport.op"] == "return")
	assert back_point.count == 1
	assert back_point.sum >= 0.02
	# Labelled like the job's own metrics, so a dashboard can filter by
	# device, and nothing per-job on a label.
	for attributes, _ in points:
		assert attributes["qfw.device.name"] == FAKE_IQM_TARGET_ID
		assert attributes["qfw.backend.kind"] == "fake-iqm"
		assert "qfw.qpm.cid" not in attributes


class _RecordingEndpoint:
	"""Stands in for the client's remote event API, the thing the QPM pushes to."""
	instances = []

	def __init__(self, class_id=None, target=None, **kwargs):
		self.target = target
		self.events = []
		self.contexts = []
		_RecordingEndpoint.instances.append(self)

	def put(self, event):
		from opentelemetry import trace
		self.contexts.append(trace.get_current_span().get_span_context())
		self.events.append(event)


@pytest.mark.filterwarnings(
	"error::pytest.PytestUnhandledThreadExceptionWarning")
def test_event_push_joins_the_job_trace_when_transport_is_on(monkeypatch, tmp_path, recording):
	import util.qpm.util_qpm as util_qpm

	_transport_on(monkeypatch)
	_setup(monkeypatch)
	configure_fake_credentials(monkeypatch, tmp_path, "trace-user")
	monkeypatch.setattr(util_qpm, "BaseEventAPI", _RecordingEndpoint)
	_RecordingEndpoint.instances.clear()
	qpm = _fake_iqm_qpm()
	decision = _reserve(qpm, "trace-user")
	qpm.register_event_notification("client-endpoint", 1, "client-class")

	with qfw_telemetry.tracer().start_as_current_span("qfw.app.job") as job:
		response = qpm.async_run(
			dict(_CIRCUIT), reservation_id=decision["reservation_id"])
	completion = _wait_for_completion(
		qpm, response["cid"], decision["reservation_id"])
	assert completion["outcome"] == "COMPLETED"
	(endpoint,) = _RecordingEndpoint.instances
	deadline = time.monotonic() + 1.0
	while not endpoint.events and time.monotonic() < deadline:
		time.sleep(0.01)
	assert len(endpoint.events) == 1

	# The push ran in the run queue's thread after the receive handler had
	# returned, and still joined the job's trace under the receive span.
	spans = recording.spans_by_name()
	(push,) = spans["qfw.transport.rpc"]
	(receive,) = spans["qfw.qpm.receive"]
	assert push.attributes["qfw.transport.op"] == "event"
	assert push.attributes["qfw.outcome"] == "completed"
	assert push.context.trace_id == job.get_span_context().trace_id
	assert push.parent.span_id == receive.context.span_id
	assert endpoint.contexts == [push.context]

	points = recording.points("qfw.transport.duration")
	assert [a["qfw.transport.op"] for a, _ in points] == ["event"]


def test_transport_return_skips_a_window_the_clocks_disagree_on(monkeypatch, recording):
	_transport_on(monkeypatch)
	# A completion stamped in the future, as a skewed provider clock would.
	instrumentation.record_transport_return({"cid": "cid-2", "completion_time": time.time() + 60})
	assert recording.spans_by_name() == {}
	assert recording.points("qfw.transport.duration") == []


def test_everything_is_inert_with_telemetry_off():
	assert not instrumentation.enabled()
	circuit = types.SimpleNamespace(
		info={"qtask_id": 7}, get_cid=lambda: "cid-off",
		creation_time=1.0, resources_consumed_time=2.0)

	with instrumentation.backend_execution(circuit, "simulator") as span:
		assert span is None
		with instrumentation.backend_phase("submit") as phase:
			assert phase is None
		with instrumentation.qpm_transpile() as transpile:
			assert transpile is None
	with instrumentation.qpm_receive() as receive:
		assert receive is None
	instrumentation.bind_circuit(circuit, qtask_id=7)
	instrumentation.record_queue(circuit)
	instrumentation.set_attribute("qfw.vendor.job_id", "j")
	instrumentation.add_event("poll")
	assert instrumentation.start_job("job", 1, 1) is None
	instrumentation.end_job(None, instrumentation.OUTCOME_COMPLETED)
	instrumentation.finish_backend_execution(None, circuit)
	with instrumentation.app_prepare(circuit) as prepare:
		assert prepare is None
	instrumentation.describe_payload(None, {})
	assert not hasattr(circuit, "otel_context")
