import json
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "backends"))
sys.path.insert(0, str(_REPO_ROOT / "DEFw" / "python" / "infra"))

import qfw_telemetry as telemetry

try:
    import defw_trace
except ImportError:  # DEFw submodule without the trace-context seam
    defw_trace = None

# Only ONE test in this module may install a real provider. OpenTelemetry
# refuses to override an already-set global TracerProvider or MeterProvider,
# so a second configure() in the same process silently reuses the first
# provider, and if that one was shut down it records nothing. Tests that need
# a live provider therefore assert against a single configured run. Everything
# else exercises the off profile or the config helpers directly.


@pytest.fixture(autouse=True)
def clean_telemetry_env(monkeypatch):
    """Every test starts from an unconfigured process with no QFW_TELEMETRY_*."""
    for name in (telemetry.TELEMETRY_ENV,
                 telemetry.TELEMETRY_DIR_ENV,
                 telemetry.TELEMETRY_SAMPLE_ENV,
                 telemetry.TELEMETRY_TRANSPORT_ENV,
                 telemetry.TELEMETRY_ENDPOINT_ENV):
        monkeypatch.delenv(name, raising=False)
    yield
    telemetry.shutdown()


def test_profile_defaults_to_off_and_accepts_aliases(monkeypatch):
    assert telemetry._profile() == telemetry.PROFILE_OFF
    for value in ("off", "no", "false", "0", ""):
        monkeypatch.setenv(telemetry.TELEMETRY_ENV, value)
        assert telemetry._profile() == telemetry.PROFILE_OFF
    for value in ("file", "yes", "true", "1"):
        monkeypatch.setenv(telemetry.TELEMETRY_ENV, value)
        assert telemetry._profile() == telemetry.PROFILE_FILE


def test_unrecognised_profile_fails_closed(monkeypatch):
    # A typo must not silently disable telemetry a benchmark depends on, nor
    # silently enable it in production.
    monkeypatch.setenv(telemetry.TELEMETRY_ENV, "grafana")
    with pytest.raises(ValueError, match=telemetry.TELEMETRY_ENV):
        telemetry._profile()


def test_sampler_rejects_out_of_range_and_nonsense_ratios(monkeypatch):
    for value in ("1.7", "-0.1", "sometimes"):
        monkeypatch.setenv(telemetry.TELEMETRY_SAMPLE_ENV, value)
        with pytest.raises(ValueError, match=telemetry.TELEMETRY_SAMPLE_ENV):
            telemetry._build_sampler()


def test_metric_export_interval_defaults_short_and_honours_otel_env(
        monkeypatch):
    monkeypatch.delenv(telemetry.METRIC_EXPORT_INTERVAL_ENV, raising=False)
    assert telemetry._metric_export_interval_ms() == \
        telemetry.DEFAULT_METRIC_EXPORT_INTERVAL_MS
    monkeypatch.setenv(telemetry.METRIC_EXPORT_INTERVAL_ENV, "2500")
    assert telemetry._metric_export_interval_ms() == 2500.0
    for value in ("0", "-5", "soon"):
        monkeypatch.setenv(telemetry.METRIC_EXPORT_INTERVAL_ENV, value)
        with pytest.raises(ValueError,
                           match=telemetry.METRIC_EXPORT_INTERVAL_ENV):
            telemetry._metric_export_interval_ms()


def test_otlp_endpoint_names_the_collector_not_the_signal(monkeypatch):
    # An endpoint handed to the OTLP/HTTP exporters is used verbatim, so the
    # signal path has to be appended here or the collector answers 404.
    monkeypatch.delenv(telemetry.TELEMETRY_ENDPOINT_ENV, raising=False)
    assert telemetry._otlp_endpoint("v1/traces") is None
    for value in ("http://otel-collector:4318", "http://otel-collector:4318/"):
        monkeypatch.setenv(telemetry.TELEMETRY_ENDPOINT_ENV, value)
        assert telemetry._otlp_endpoint("v1/traces") == \
            "http://otel-collector:4318/v1/traces"
        assert telemetry._otlp_endpoint("v1/metrics") == \
            "http://otel-collector:4318/v1/metrics"
    monkeypatch.setenv(
        telemetry.TELEMETRY_ENDPOINT_ENV, "http://c:4318/v1/traces")
    assert telemetry._otlp_endpoint("v1/traces") == "http://c:4318/v1/traces"


def test_export_dir_prefers_explicit_setting(monkeypatch, tmp_path):
    monkeypatch.setenv(telemetry.TELEMETRY_DIR_ENV, str(tmp_path))
    assert telemetry._export_dir() == str(tmp_path)


def test_export_dir_defaults_node_local(monkeypatch, tmp_path):
    # Never a shared filesystem, because export contention would perturb what
    # is being measured.
    monkeypatch.delenv("DEFW_TMP_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    assert telemetry._export_dir() == str(
        tmp_path / telemetry.DEFAULT_TELEMETRY_DIRNAME)


def test_off_profile_is_inert(tmp_path, monkeypatch):
    monkeypatch.setenv(telemetry.TELEMETRY_DIR_ENV, str(tmp_path))
    assert telemetry.configure("qfw-qpm", "0.1", role="qpm") == \
        telemetry.PROFILE_OFF
    assert telemetry.enabled() is False

    with telemetry.tracer().start_as_current_span("qfw.qpm.receive") as span:
        assert span.is_recording() is False
        span.set_attribute("qfw.stack.api_path", "qrmi")
    telemetry.duration_histogram("qfw.qpm.duration").record(
        0.1, {"qfw.qpm.op": "receive"})
    telemetry.counter("qfw.app.job.count").add(1, {"qfw.outcome": "completed"})

    assert list(tmp_path.iterdir()) == []


def test_adopted_providers_record_without_touching_the_global_slot():
    # A host that owns its OpenTelemetry setup, or a test that needs a fresh
    # recording provider per test, hands providers in. The global providers
    # stay as they were, so this can run before or after configure().
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter)
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON

    before = otel_trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    provider = sdk_trace.TracerProvider(sampler=ALWAYS_ON)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    assert telemetry.use_providers(provider) == telemetry.PROFILE_FILE
    assert telemetry.enabled() is True
    assert otel_trace.get_tracer_provider() is before
    if defw_trace is not None:
        assert defw_trace.hooks_registered() is True

    with telemetry.tracer().start_as_current_span("qfw.app.job") as span:
        assert span.is_recording() is True
    # No meter was handed in, so metrics degrade to no-ops rather than fail.
    telemetry.duration_histogram("qfw.app.job.duration").record(1.0, {})
    telemetry.counter("qfw.app.job.count").add(1, {})
    assert [s.name for s in exporter.get_finished_spans()] == ["qfw.app.job"]

    telemetry.shutdown()
    assert telemetry.enabled() is False
    if defw_trace is not None:
        assert defw_trace.hooks_registered() is False


def test_defw_hooks_are_not_registered_when_telemetry_is_off():
    # Nothing should touch the RPC path when telemetry is off.
    telemetry.configure("qfw-qpm", "0.1")
    assert telemetry._STATE.defw_hooks is False
    if defw_trace is not None:
        assert defw_trace.hooks_registered() is False


def test_transport_spans_stay_off_without_a_provider(monkeypatch):
    # The flag alone must not enable a span that has nowhere to go. Guarded
    # call sites then cost a boolean test.
    monkeypatch.setenv(telemetry.TELEMETRY_TRANSPORT_ENV, "1")
    telemetry.configure("qfw-defw", "0.1")
    assert telemetry.transport_spans_enabled() is False


def test_logs_level_defaults_off_and_fails_closed(monkeypatch):
    import logging
    assert telemetry._logs_level() is None
    for value in ("off", "0", "no", "false", ""):
        monkeypatch.setenv(telemetry.TELEMETRY_LOGS_ENV, value)
        assert telemetry._logs_level() is None
    for value, level in (("error", logging.ERROR), ("Warning", logging.WARNING),
                         ("info", logging.INFO), ("debug", logging.DEBUG)):
        monkeypatch.setenv(telemetry.TELEMETRY_LOGS_ENV, value)
        assert telemetry._logs_level() == level
    monkeypatch.setenv(telemetry.TELEMETRY_LOGS_ENV, "loud")
    with pytest.raises(ValueError, match=telemetry.TELEMETRY_LOGS_ENV):
        telemetry._logs_level()


def test_adopted_logger_provider_exports_root_logger_records_with_the_span():
    import logging
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.trace import TracerProvider
    try:
        from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    except ImportError:  # older SDKs
        from opentelemetry.sdk._logs.export import (
            InMemoryLogExporter as InMemoryLogRecordExporter)

    exporter = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    root = logging.getLogger()
    before = list(root.handlers)
    telemetry.use_providers(
        TracerProvider(), logger_provider=logger_provider,
        logs_level=logging.WARNING)
    assert telemetry.logs_enabled() is True
    handler = telemetry._STATE.log_handler
    assert handler in root.handlers
    # DEFw registers its categories as level names 30 to 35.
    logging.addLevelName(33, "DEFW_APP")
    logging.addLevelName(34, "DEFW_RPC")
    try:
        with telemetry.tracer().start_as_current_span("qfw.app.job") as job:
            # What a service writes about the job goes out at warning...
            logging.getLogger("defw.qpm").log(33, "circuit %s queued", "c-1")
            # ...the transport's own chatter does not, whatever its level.
            logging.getLogger("defw.workers").log(34, "handling request")
            logging.getLogger("qfw.client").info("too quiet for this tier")
            logging.getLogger("opentelemetry.sdk.trace").warning(
                "the exporter's own noise stays local")
        logging.getLogger("defw.qpm").warning("after the job, no span")
    finally:
        telemetry.shutdown()

    assert handler not in root.handlers
    assert root.handlers == before
    finished = getattr(exporter, "get_finished_log_records",
                       getattr(exporter, "get_finished_logs", None))()
    bodies = [(r.log_record.body, r.log_record.trace_id) for r in finished]
    assert bodies == [
        ("circuit c-1 queued", job.get_span_context().trace_id),
        ("after the job, no span", 0),
    ]


def test_debug_tier_carries_defw_internals_too():
    import logging
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.sdk.trace import TracerProvider
    try:
        from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
    except ImportError:  # older SDKs
        from opentelemetry.sdk._logs.export import (
            InMemoryLogExporter as InMemoryLogRecordExporter)

    exporter = InMemoryLogRecordExporter()
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    logging.addLevelName(34, "DEFW_RPC")
    root = logging.getLogger()
    level_before = root.level
    telemetry.use_providers(
        TracerProvider(), logger_provider=logger_provider,
        logs_level=logging.DEBUG)
    try:
        root.setLevel(logging.DEBUG)
        logging.getLogger("defw.workers").log(34, "handling request")
        logging.getLogger("qfw.client").debug("every detail")
    finally:
        root.setLevel(level_before)
        telemetry.shutdown()
    finished = getattr(exporter, "get_finished_log_records",
                       getattr(exporter, "get_finished_logs", None))()
    assert [r.log_record.body for r in finished] == [
        "handling request", "every detail"]


def _attr_value(value):
    """Unwrap one OTLP AnyValue into a plain Python value."""
    for key in ("stringValue", "boolValue", "arrayValue"):
        if key in value:
            return value[key]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    return None


def _attrs(attribute_list):
    return {a["key"]: _attr_value(a["value"]) for a in attribute_list}


def _read_otlp_logs(path):
    """Flatten OTLP/JSON log export lines into (records, resource_attributes)."""
    records = []
    resource = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        for resource_logs in json.loads(line)["resourceLogs"]:
            resource = _attrs(resource_logs["resource"]["attributes"])
            for scope_logs in resource_logs["scopeLogs"]:
                records.extend(scope_logs["logRecords"])
    return records, resource


def _read_otlp_spans(path):
    """Flatten OTLP/JSON export lines into (spans, resource_attributes)."""
    spans = []
    resource = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        for resource_spans in json.loads(line)["resourceSpans"]:
            resource = _attrs(resource_spans["resource"]["attributes"])
            for scope_spans in resource_spans["scopeSpans"]:
                spans.extend(scope_spans["spans"])
    return spans, resource


@pytest.mark.skipif(defw_trace is None,
                    reason="DEFw submodule has no trace-context seam")
def test_file_profile_stitches_a_trace_across_the_rpc_boundary(
        tmp_path, monkeypatch):
    monkeypatch.setenv(telemetry.TELEMETRY_ENV, telemetry.PROFILE_FILE)
    monkeypatch.setenv(telemetry.TELEMETRY_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(telemetry.TELEMETRY_SAMPLE_ENV, telemetry.SAMPLE_ALWAYS)
    monkeypatch.setenv(telemetry.TELEMETRY_LOGS_ENV, "warning")

    assert telemetry.configure(
        "qfw-qpm", "0.1", role="qpm",
        attributes={"qfw.device.name": "q20", "ignored": None,
                    "service.name": "not-this-one"}) == \
        telemetry.PROFILE_FILE
    assert telemetry.enabled() is True

    assert telemetry._STATE.defw_hooks is True

    # Caller side. DEFw builds this carrier while the client span is current.
    with telemetry.tracer().start_as_current_span("qfw.app.job") as job:
        assert job.is_recording() is True
        carrier = defw_trace.inject()
        import logging
        logging.getLogger("defw.client").log(33, "submitting %d circuit", 1)
    assert carrier["traceparent"].startswith("00-")

    # Remote side, as handle_rpc_req does it: attach the received context so
    # the work joins the caller's trace rather than starting a new one.
    token = defw_trace.attach(carrier)
    try:
        with telemetry.tracer().start_as_current_span("qfw.qpm.receive"):
            pass
    finally:
        defw_trace.detach(token)

    telemetry.duration_histogram("qfw.qpm.duration").record(
        0.0042, {"qfw.qpm.op": "receive"})
    counter = telemetry.counter("qfw.app.job.count")
    assert telemetry.counter("qfw.app.job.count") is counter
    counter.add(1, {"qfw.outcome": "completed"})
    telemetry.shutdown()

    exports = sorted(tmp_path.glob("*.spans.jsonl"))
    assert len(exports) == 1
    spans, resource = _read_otlp_spans(exports[0])

    # The logs tier wrote the DEFw-level line, stitched to the job's trace
    # and on the same resource, in its own file.
    log_exports = sorted(tmp_path.glob("*.logs.jsonl"))
    assert len(log_exports) == 1
    records, log_resource = _read_otlp_logs(log_exports[0])
    assert [r["body"]["stringValue"] for r in records] == ["submitting 1 circuit"]
    assert records[0]["traceId"] == next(
        s["traceId"] for s in spans if s["name"] == "qfw.app.job")
    assert log_resource["service.name"] == "qfw-qpm"

    by_name = {span["name"]: span for span in spans}
    assert set(by_name) == {"qfw.app.job", "qfw.qpm.receive"}
    # One trace, not two. Without context propagation the remote hop would
    # open its own trace and no per-hop breakdown would be reconstructable.
    assert len({span["traceId"] for span in spans}) == 1
    # The remote span is parented to the caller's span across the boundary.
    assert by_name["qfw.qpm.receive"]["parentSpanId"] == \
        by_name["qfw.app.job"]["spanId"]
    assert "parentSpanId" not in by_name["qfw.app.job"]

    # Resource is written once per export line, not repeated per span.
    assert resource["service.name"] == "qfw-qpm"
    assert resource["qfw.component.role"] == "qpm"
    assert resource["qfw.conventions.version"] == \
        telemetry.CONVENTIONS_VERSION
    # Caller-supplied resource attributes ride along; None is dropped and
    # the fixed keys are not overridable.
    assert resource["qfw.device.name"] == "q20"
    assert "ignored" not in resource

    # OTLP/JSON departs from the protobuf JSON mapping and requires hex
    # identifiers, where protobuf would emit these bytes fields as base64.
    # This guards the conversion that keeps the output readable by OTLP
    # tooling.
    for span in spans:
        assert re.fullmatch(r"[0-9a-f]{32}", span["traceId"]), span["traceId"]
        assert re.fullmatch(r"[0-9a-f]{16}", span["spanId"]), span["spanId"]
    assert re.fullmatch(
        r"[0-9a-f]{16}", by_name["qfw.qpm.receive"]["parentSpanId"])

    metrics = sorted(tmp_path.glob("*.metrics.jsonl"))
    assert metrics
    names = set()
    for line in metrics[0].read_text().splitlines():
        if not line.strip():
            continue
        for resource_metrics in json.loads(line)["resourceMetrics"]:
            for scope_metrics in resource_metrics["scopeMetrics"]:
                for metric in scope_metrics["metrics"]:
                    names.add(metric["name"])
    assert {"qfw.qpm.duration", "qfw.app.job.count"} <= names
