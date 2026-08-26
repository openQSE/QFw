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

    assert list(tmp_path.iterdir()) == []


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

    assert telemetry.configure("qfw-qpm", "0.1", role="qpm") == \
        telemetry.PROFILE_FILE
    assert telemetry.enabled() is True

    assert telemetry._STATE.defw_hooks is True

    # Caller side. DEFw builds this carrier while the client span is current.
    with telemetry.tracer().start_as_current_span("qfw.app.job") as job:
        assert job.is_recording() is True
        carrier = defw_trace.inject()
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
    telemetry.shutdown()

    exports = sorted(tmp_path.glob("*.spans.jsonl"))
    assert len(exports) == 1
    spans, resource = _read_otlp_spans(exports[0])

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

    # OTLP/JSON departs from the protobuf JSON mapping and requires hex
    # identifiers, where protobuf would emit these bytes fields as base64.
    # This guards the conversion that keeps the output readable by OTLP
    # tooling.
    for span in spans:
        assert re.fullmatch(r"[0-9a-f]{32}", span["traceId"]), span["traceId"]
        assert re.fullmatch(r"[0-9a-f]{16}", span["spanId"]), span["spanId"]
    assert re.fullmatch(
        r"[0-9a-f]{16}", by_name["qfw.qpm.receive"]["parentSpanId"])

    assert sorted(tmp_path.glob("*.metrics.jsonl"))
