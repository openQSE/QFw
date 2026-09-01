# qfw_telemetry

OpenTelemetry bootstrap for QFw. Owns provider setup, the deployment profile,
and the sampling policy described in
[`docs/benchmarking-design.md`](../../docs/benchmarking-design.md).

This package emits no spans and no metrics of its own. Instrumentation sites
import the accessors below.

## Quick start

Configure once per process, at startup:

```python
import qfw_telemetry

qfw_telemetry.configure("qfw-qpm", service_version="0.1", role="qpm")
```

Then instrument:

```python
from qfw_telemetry import tracer, duration_histogram

with tracer().start_as_current_span("qfw.qpm.receive") as span:
	span.set_attribute("qfw.stack.api_path", "qrmi")
	...

duration_histogram("qfw.qpm.duration").record(
	elapsed_s, {"qfw.qpm.op": "receive"})
```

Nothing here is required. With no OpenTelemetry SDK installed, or with the
profile off, every accessor degrades to a no-op and QFw runs unchanged.

## Configuration

Entirely by environment variable, so selecting a profile is a deployment
change and never a code change.

| Variable | Values | Default | Meaning |
| --- | --- | --- | --- |
| `QFW_TELEMETRY` | `off`, `file`, `otlp` | `off` | Deployment profile |
| `QFW_TELEMETRY_SAMPLE` | `off`, `always`, ratio | `off` | Trace sampling |
| `QFW_TELEMETRY_DIR` | path | node-local tmp | Export directory, file profile |
| `QFW_TELEMETRY_TRANSPORT` | `0`, `1` | `0` | DEFw RPC spans |
| `QFW_TELEMETRY_ENDPOINT` | URL | SDK default | Collector, otlp profile |

Two behaviours are deliberate:

- **Bad config fails closed.** An unrecognised profile or sample ratio raises
  rather than defaulting. A typo cannot silently disable telemetry that a
  benchmark run depends on, nor silently enable it in production.
- **A missing SDK warns and continues.** Telemetry must never be a hard
  dependency, but an operator who asked for a profile should not get an empty
  run with no explanation.

### Typical settings

Production, continuous per-hop metrics with traces off:

```
QFW_TELEMETRY=file
QFW_TELEMETRY_SAMPLE=off
```

A benchmark run, full traces:

```
QFW_TELEMETRY=file
QFW_TELEMETRY_SAMPLE=always
QFW_TELEMETRY_DIR=/node/local/path
```

Studying the transport itself, and only then:

```
QFW_TELEMETRY_TRANSPORT=1
```

## Cost, and why the guards exist

Turning tracing off does not make a call site free. A sampled-out span still
costs microseconds in Python, because the context manager runs whatever the
sampler decides. The sampler avoids the recording work, not the call-site
work.

Measured with `opentelemetry-sdk` 1.44.0 on CPython 3.14, single-threaded,
null exporter behind a `BatchSpanProcessor`:

| State | Cost per call site |
| --- | --- |
| Flag-guarded off | ~24 ns |
| Sampled out | ~3.6 us |
| Recording | ~10 us |
| Metric histogram record | ~1.6 us |

That difference does not matter on the job path. Roughly 11 spans and 9 metric
records per job is about 55 us with traces sampled off, against measured job
durations of 1 to 4 seconds, so well under a hundredth of a percent.

It matters wherever the operation being measured is itself only microseconds.
DEFw RPC round-trips on a fast transport are in that range, which is why
`qfw.transport.rpc` is flag-guarded rather than sampled out.

### Three rules for instrumentation sites

**Cold paths may use the plain form.** The per-job budget absorbs it.

```python
with tracer().start_as_current_span("qfw.qpm.receive"):
	...
```

**Hot paths must guard the call site so it does not execute at all.**

```python
if transport_spans_enabled():
	with tracer().start_as_current_span("qfw.transport.rpc"):
		...
```

**Expensive attribute values need `is_recording()`, not `enabled()`.**
Arguments are evaluated before a non-recording span discards them.

```python
if span.is_recording():
	span.set_attribute("qfw.circuit.depth_transpiled", circuit.depth())
```

`enabled()` reports that a provider is installed, **not** that anything is
recording. Telemetry can be on while traces are sampled off, which is exactly
the always-on metrics tier the design relies on for per-hop attribution.

## What the file profile writes

OTLP/JSON, one export batch per line. The SDK ships no file exporter, so
[`_otlp_json.py`](_otlp_json.py) supplies one for traces and metrics using the
OTLP encoders.

OTLP/JSON rather than the SDK's `span.to_json()` for two reasons. Files stay
replayable into a collector with stock components, which is what makes the
composable deployment profiles real rather than aspirational. And the resource
block is written once per batch instead of once per span, measured at 42%
fewer bytes per span, a gap that widens once run context lands on the
resource.

One subtlety worth knowing before touching that exporter: OTLP/JSON departs
from the standard protobuf JSON mapping and requires **hex** trace and span
identifiers, where protobuf encodes those bytes fields as base64.
`MessageToDict` produces base64, so the identifiers are rewritten on the way
out. Without that step the output is protobuf JSON of an OTLP message rather
than OTLP/JSON, and a collector reading it back would reject the identifiers.
A test asserts 32 and 16 character hex so this cannot regress silently.

## Trace context across DEFw RPC

A distributed trace only holds together if the caller's context reaches the
remote it invokes. DEFw carries an opaque carrier through its RPC envelope and
scopes it around the remote dispatch, but knows nothing about OpenTelemetry
and depends on nothing. The propagator is registered from `configure()`.

A DEFw build without that seam is handled rather than required. The import is
optional, and when it is missing `configure()` logs that traces will not
stitch across RPC boundaries and carries on, leaving per-process traces.

## Caveats

**Configure once per process.** OpenTelemetry refuses to replace a global
provider that is already set, so calling `shutdown()` and then `configure()`
again does not rebuild a working provider. `shutdown()` is for flushing on the
way out, not for cycling telemetry back up. The same constraint means only one
test per process can install a real provider.

**Export to node-local storage, never a shared filesystem.** Export contention
would perturb what is being measured.

**Span rates must stay bounded per job.** The per-job budget above only holds
if no span is emitted per unit of waiting. This is why `qfw.backend.collect`
is one span per job with polls recorded as span events, rather than one span
per poll.
