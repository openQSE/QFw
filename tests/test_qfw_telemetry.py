import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backends"))

import qfw_telemetry as telemetry

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


def test_file_profile_writes_a_parented_trace(tmp_path, monkeypatch):
    monkeypatch.setenv(telemetry.TELEMETRY_ENV, telemetry.PROFILE_FILE)
    monkeypatch.setenv(telemetry.TELEMETRY_DIR_ENV, str(tmp_path))
    monkeypatch.setenv(telemetry.TELEMETRY_SAMPLE_ENV, telemetry.SAMPLE_ALWAYS)

    assert telemetry.configure("qfw-qpm", "0.1", role="qpm") == \
        telemetry.PROFILE_FILE
    assert telemetry.enabled() is True

    with telemetry.tracer().start_as_current_span("qfw.app.job") as job:
        assert job.is_recording() is True
        with telemetry.tracer().start_as_current_span("qfw.qpm.receive"):
            pass
    telemetry.duration_histogram("qfw.qpm.duration").record(
        0.0042, {"qfw.qpm.op": "receive"})
    telemetry.shutdown()

    exports = sorted(tmp_path.glob("*.spans.jsonl"))
    assert len(exports) == 1
    spans, resource = _read_otlp_spans(exports[0])

    by_name = {span["name"]: span for span in spans}
    assert set(by_name) == {"qfw.app.job", "qfw.qpm.receive"}
    # The child carries the parent link, which is what makes a per-hop
    # breakdown reconstructable without cross-node timestamp subtraction.
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
