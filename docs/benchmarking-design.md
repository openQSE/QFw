# QFw Benchmarking and Profiling Design (Proposal)

Status: **draft, revision 2 — request for community input**

This document proposes adding benchmarking and profiling support to QFw. It
is intended as a starting point for discussion: the semantic conventions and
suite-integration plans in particular are initial proposals, and community
members are encouraged to suggest additions, removals, or changes. See
[Open Questions and Community Input](#open-questions-and-community-input).

Revision 2 incorporates the first round of review feedback (PR #30):

- **OpenTelemetry is adopted** as the telemetry data model and SDK layer,
  replacing the bespoke `QFW_BENCH` record format proposed in revision 1.
- The document's core contribution is recast as **semantic conventions**
  (span names and `qfw.*` attributes) for quantum benchmarking.
- A **benchmark suite landscape** section is added covering SupermarQ,
  QStone, and the MQSS Benchmarking Framework.
- A third **streaming deployment profile** is added for sites with existing
  telemetry infrastructure (Kafka → VictoriaMetrics), together with
  **attribute cardinality classes** so the conventions are safe to use as
  metric labels in such a store.
- A short **licensing** section is added.
- The document is renamed "Benchmarking and Profiling Design" — Type B
  below is profiling/tracing.

## Table Of Contents

- [Motivation](#motivation)
- [Two Kinds of Benchmarking](#two-kinds-of-benchmarking)
- [Design Principles](#design-principles)
- [Telemetry Design: OpenTelemetry](#telemetry-design-opentelemetry)
  - [What Is Adopted](#what-is-adopted)
  - [Deployment Profiles](#deployment-profiles)
  - [Instrumentation Points](#instrumentation-points)
  - [Trace-Context Propagation Through DEFw RPC](#trace-context-propagation-through-defw-rpc)
  - [Clocks, Precision, and Overhead](#clocks-precision-and-overhead)
- [Semantic Conventions (Initial Proposal)](#semantic-conventions-initial-proposal)
  - [Span Vocabulary](#span-vocabulary)
  - [Context Attributes](#context-attributes)
    - [Cardinality Classes](#cardinality-classes)
  - [Metrics](#metrics)
  - [Result and Quality Data](#result-and-quality-data)
- [Benchmark Suite Integrations](#benchmark-suite-integrations)
  - [SupermarQ](#supermarq)
  - [QStone](#qstone)
  - [MQSS Benchmarking Framework](#mqss-benchmarking-framework)
  - [Common Integration Notes](#common-integration-notes)
- [Report Generation Design](#report-generation-design)
  - [Part 1: The JSON Report (Extractor)](#part-1-the-json-report-extractor)
  - [Part 2: The Human-Readable Report (Renderer)](#part-2-the-human-readable-report-renderer)
  - [Comparison Reports](#comparison-reports)
- [Licensing](#licensing)
- [Implementation Phases](#implementation-phases)
- [Open Questions and Community Input](#open-questions-and-community-input)

## Motivation

QFw sits in an unusual position: it orchestrates quantum workloads across
multiple simulators (NWQ-Sim, TNQVM, and others), real hardware (IQM), and,
with the QRMI/QDMI shim work, multiple resource-management APIs reaching the
*same* device. That makes it a natural neutral testbed for comparing quantum
software libraries, resource-management interfaces, and vendor
implementations under one scheduler with consistent metadata.

At the same time, QFw is itself a piece of HPC middleware whose overhead
matters, especially for hybrid quantum-classical algorithms where the
framework sits inside the iteration loop.

This proposal covers both, with a shared telemetry and reporting
infrastructure.

QFw's value here is **not** defining new benchmark circuits or metrics:
established suites already exist (SupermarQ, QStone, MQT Bench, the MQSS
Benchmarking Framework, QED-C Application-Oriented Benchmarks, Qiskit
Benchpress). QFw's value is being the neutral **execution harness and
measurement rig** that can run such suites across back-ends and API paths,
and attach consistent context (device calibration, software versions, timing
breakdowns) to every result.

## Two Kinds of Benchmarking

### Type A: Benchmarking *through* QFw (QFw as testbed)

The same workload is executed against different back-ends or through
different software paths, and the results are compared.

| Comparison axis | What varies | Example question |
| --- | --- | --- |
| Vendor / device | The QPU or simulator behind the QPM service | How does device X compare to device Y on an application suite? |
| Resource-management API | Native vendor client vs QRMI vs QDMI, same device | What overhead and behavioral differences does each API layer introduce? |
| Library / compiler | Front-end or transpiler settings, same abstract circuit | Which toolchain produces shallower circuits for this ISA, and at what compile-time cost? |
| Simulator scaling | Simulator choice, node/rank count, circuit size | How do NWQ-Sim and TNQVM scale with qubits, depth, and nodes on this cluster? |
| System integration under load | User population, job mix, contention | What throughput and turnaround does a realistic multi-user workload actually get? |

The API-path comparison is worth highlighting: because QFw can reach the same
IQM hardware through the native IQM client path, through QRMI, and through
QDMI, it can isolate the cost and behavior of the resource-management layer
itself — data directly relevant to ongoing interface-convergence discussions.

### Type B: Profiling *of* QFw (framework overhead)

QFw's own contribution to end-to-end latency and throughput is measured.
This is distributed tracing/profiling in the classic sense, which is why
OpenTelemetry traces (below) are its natural vehicle.

| Measurement | Why it matters |
| --- | --- |
| Orchestration latency per hop (submit → QPM → QRC → back-end client) | Locates where time goes inside the framework |
| Serialization / canonicalization cost (circuit → OpenQASM3) | Fixed per-job overhead |
| Hybrid-loop round-trip latency | For VQE/QAOA, per-iteration framework latency often dominates user-visible runtime |
| Job throughput under load | Concurrent jobs, batching behavior, scheduler interaction |
| DEFw RPC / transport cost | Baseline for the libfabric (TCP vs OFI) transport work, which will consume this same instrumentation |

Both types share the same telemetry pipeline and report tooling. Type B
spans are recorded during every Type A run: a vendor comparison report
automatically includes the framework-overhead breakdown.

## Design Principles

1. **Open standards, not bespoke formats.** Telemetry is emitted through
   OpenTelemetry — traces, metrics, and logs in OTLP — never as free-form
   log text to be regexed later, and not as a QFw-specific record format.
   QFw defines *semantics* (span names, attributes) on top of the standard,
   not a new envelope.
2. **Capture time-varying context at run time.** Anything that could differ
   between execution time and report time (device calibration, transpiler
   settings, queue state) is recorded when the run happens. The report
   tooling *aggregates and formats*; it never *discovers* anything that
   could have changed since the run. Static facts (package versions,
   topology) may be gathered either way, but run-time capture is preferred
   for uniformity.
3. **Archivable artifacts first, dashboards second.** The canonical output
   of a benchmark run is a machine-readable JSON report derived from the
   telemetry; the human-readable report is rendered from it. Comparing runs
   means diffing structured reports, not parsing formatted text. Live
   dashboards (collector profile) complement but do not replace archivable
   artifacts.
4. **Low perturbation.** Instrumentation must not meaningfully disturb what
   it measures: batched span export, node-local telemetry files, and
   sampling controls for high-rate signals (see
   [Clocks, Precision, and Overhead](#clocks-precision-and-overhead)).
5. **Versioned semantics.** OTLP handles envelope evolution; the QFw
   semantic conventions and the JSON report schema carry explicit versions
   so old data remains interpretable as conventions evolve.
6. **Reuse existing suites.** Circuit sets and quality metrics come from
   established benchmark suites; QFw provides execution, timing, and
   context, and correlates suite results with its own traces.

## Telemetry Design: OpenTelemetry

### What Is Adopted

QFw adopts the [OpenTelemetry](https://opentelemetry.io/docs/) data model
and SDKs for all benchmarking/profiling telemetry, using all three signals:

- **Traces** are the primary signal. A benchmark run is a trace; every hop
  through the stack is a span with start/end timestamps, attributes, and a
  parent — which replaces revision 1's paired start/end events and hand-built
  `run_id` stitching with the standard trace ID + parent/child model.
- **Metrics** carry counters and duration histograms — both for throughput
  measurements and for timings that need distribution statistics or better
  effective precision than cross-node wall-clock spans provide (see
  [Clocks, Precision, and Overhead](#clocks-precision-and-overhead)).
- **Logs** remain ordinary DEFw logs; where useful, structured log entries
  are linked to the active trace ID so they can be stitched into the same
  timeline.

Adopting the standard has consequences the bespoke format could not offer:

- **Instrumentation below QFw.** Mature SDKs exist for Python (DEFw, QPM
  services), Rust (QRMI), and C/C++ (QDMI), so third-party layers below the
  shim boundary can emit spans into the same trace — exactly where the
  Type A API-path comparison gets interesting. Some QPU vendors already use
  OTel internally.
- **Observability above QFw.** SLURM has adopted OpenMetrics, and a
  [SLURM processor for the OTel Collector](https://github.com/facebookresearch/gcm/blob/main/slurmprocessor/README.md)
  exists, so scheduler-level data can be correlated with QFw traces in the
  collector profile.
- **Ecosystem tooling.** Trace stores and viewers (e.g. Grafana Tempo's
  trace waterfall) render the per-hop latency breakdown with no custom
  code, and backward-compatible addition of spans/attributes is a
  first-class feature of the model.

### Deployment Profiles

The same instrumentation supports three deployment profiles; choosing one
is a configuration decision, not a code change.

**Profile 1 — file export (default, zero infrastructure).** Each
instrumented process exports OTLP JSON to files on node-local storage
(e.g. under the DEFw `tmp` directory). Post-run tooling reads the files and
produces reports. This preserves the simple "run, then process files"
workflow and suits minimal, air-gapped, CI, and development deployments —
including the QFw-SLURM-Cluster reference deployment.

**Profile 2 — collector (optional, full observability).** The same signals
are shipped to an OTel Collector and stored/visualized with off-the-shelf
stacks (e.g. Grafana + Tempo + Loki + Mimir, or Prometheus). Docker Compose
examples and Kubernetes Helm charts for such stacks exist and can be adapted
as a reference deployment for persistent installations. The SLURM processor
above slots in here.

**Profile 3 — streaming to site telemetry infrastructure (optional).**
Sites that already operate a telemetry pipeline can receive QFw's signals
into it rather than running a QFw-specific stack. The motivating case is
ORNL, whose HPC telemetry today streams from many sources over **Apache
Kafka** into **VictoriaMetrics** for storage and analysis, and which does
not use OpenTelemetry natively. Adopting OTel does not conflict with such
a pipeline — OTel governs what QFw *emits*, while Kafka and VictoriaMetrics
are transport and storage — and the connecting pieces are stock components:

```
QFw (OTel SDK) → Collector agent ──[kafka exporter]──► site Kafka topic
                                                            │
                            ┌───────────────────────────────┘
                            ▼
        [kafka receiver] → Collector → VictoriaMetrics  (metrics, OTLP native)
                                     → VictoriaTraces   (traces, OTLP native)
                                     → VictoriaLogs     (logs, OTLP native)
```

- The OTel Collector's **Kafka exporter and receiver** are OSS
  (collector-contrib), defaulting to `otlp_proto` encoding, so QFw needs no
  Kafka-specific code.
- **VictoriaMetrics ingests OTLP metrics natively** at
  `/opentelemetry/v1/metrics` in the OSS release, and promotes OTel resource
  attributes to labels — so the `qfw.*` conventions become directly
  queryable (see the cardinality guidance in
  [Context Attributes](#context-attributes)).
- Sites whose Kafka pipeline already has consumers may only need QFw to land
  OTLP-encoded messages on a topic.

Two practical notes for this profile:

- **Traces need a trace store.** VictoriaMetrics is a metrics database;
  QFw's Type B per-hop profiling is fundamentally traces. Options, in rough
  order of preference: (a) add **VictoriaTraces** (same vendor, OTLP
  ingestion, Jaeger-compatible query APIs for Grafana — but newer, built on
  VictoriaLogs, minimum retention one day, no per-tenant authorization);
  (b) use the Collector's **spanmetrics connector** to derive
  rate/error/duration metrics from spans, sending aggregates into the
  existing metrics store while full traces stay local under profile 1 —
  usually the least invasive option for a site that does not want to adopt
  a trace store; (c) route traces to a separate store (Tempo, Jaeger)
  alongside metrics.
- **Licensing of the Kafka path.** `vmagent`'s own Kafka consumer is a
  VictoriaMetrics *Enterprise* feature, but the OSS OTel Collector Kafka
  receiver covers the same ground, so no license is required for this
  topology.

Because VictoriaMetrics is Prometheus-compatible, SLURM's OpenMetrics data
lands in the same store, keeping scheduler and framework telemetry
queryable together.

The report pipeline (below) consumes profile 1's files directly; under
profiles 2 and 3 the same reports can be generated from the backing store.
Profiles compose: a site may stream to shared infrastructure *and* keep
node-local OTLP files for archivable per-run reports.

### Instrumentation Points

Spans follow the job path through the stack. Sketch of the trace tree for
one job (span names from the [Span Vocabulary](#span-vocabulary)):

```
qfw.bench.run                      (root: one benchmark run)
└── qfw.job                        (one circuit/job, end to end)
    ├── qfw.app.serialize          (circuit → canonical OpenQASM3)
    ├── qfw.qpm.receive
    ├── qfw.qpm.transpile          (if QFw-side transpilation)
    ├── qfw.qpm.queue              (time queued inside QFw)
    ├── qfw.qrc.dispatch
    └── qfw.client.execute         (back-end client: IQM | QRMI | QDMI | simulator)
        ├── qfw.client.acquire     (session/resource acquisition)
        ├── qfw.client.submit
        └── qfw.client.poll        (per poll; count derivable)
```

Hybrid workloads wrap each iteration in a `qfw.iter` span (child of
`qfw.bench.run`, parent of that iteration's `qfw.job` spans). DEFw RPC
round-trips can be recorded as `qfw.rpc` spans — off by default, enabled
for transport work (libfabric TCP vs OFI).

### Trace-Context Propagation Through DEFw RPC

OTel auto-instruments HTTP and gRPC, but DEFw uses its own RPC transport,
so W3C `traceparent` context must be injected into and extracted from DEFw
RPC envelopes manually. This is a Phase 1 work item and the main
QFw-specific engineering cost of the OTel adoption. Once done, spans
emitted by any service on any node join the correct trace automatically —
and the same propagation must be preserved by the future libfabric
transport.

### Clocks, Precision, and Overhead

- **Span timestamps are wall-clock.** Within one process they yield
  accurate durations; *across* nodes, alignment is limited by clock
  synchronization quality (at best the resolution of a well-conditioned
  NTP/PTP deployment). Per-hop conclusions should therefore rest on span
  durations, not on cross-node timestamp differences.
- **Metrics for finer precision.** Where measurements need resolution below
  cross-node clock sync, or where distributions matter (e.g. per-iteration
  latency percentiles at scale), duration histograms measured with a
  monotonic clock inside one process are the right signal, complementing
  the trace view.
- **Overhead.** The SDKs' batch span processors move export off the hot
  path, and expected span rates are modest (tens of spans per job; order
  ten per iteration in hybrid loops with ≥100 ms iterations) —
  perturbation well below run-to-run noise. Deployment rules: export to
  node-local storage, never a shared filesystem; leave high-rate span
  sources (`qfw.rpc`) off unless the transport itself is under study; do
  not run benchmarks with verbose DEFw debug logging enabled.

## Semantic Conventions (Initial Proposal)

OTel provides the envelope but not the semantics: there are no existing
conventions for calibration set IDs, transpiled circuit depth, shot counts,
or which resource-management path a job took. Defining these — clearly and
vendor-neutrally — is this document's core contribution, and the area where
community input is most valuable. A shared set of quantum-benchmarking
semantic conventions could outlive QFw itself.

Conventions are versioned via the standard OTel `schema_url` / attribute
`qfw.conventions.version` (starting at `1`).

### Span Vocabulary

| Span | Emitted by | Measures |
| --- | --- | --- |
| `qfw.bench.run` | front-end / scenario driver | The whole benchmark run; carries run label and workload attributes |
| `qfw.iter` | front-end | One hybrid-algorithm iteration; attribute `qfw.iter.index` |
| `qfw.job` | front-end (`qfw_qiskit`) | One circuit/job end to end, submission to result delivery |
| `qfw.app.serialize` | `qfw_qiskit` | Circuit → canonical OpenQASM3 conversion; payload size attribute |
| `qfw.qpm.receive` | QPM service | Job arrival and admission at the QPM |
| `qfw.qpm.transpile` | QPM service | QFw-side transpilation; pre/post circuit-statistics attributes |
| `qfw.qpm.queue` | QPM service | Time queued inside QFw before dispatch |
| `qfw.qrc.dispatch` | QRC | Hand-off to the back-end client |
| `qfw.client.execute` | back-end client | Full back-end interaction; attribute `qfw.stack.api_path` = `native` \| `qrmi` \| `qdmi` \| `simulator` |
| `qfw.client.acquire` | back-end client | Resource/session acquisition (QRMI `acquire`, QDMI session open, vendor connect) |
| `qfw.client.submit` | back-end client | The submission call to the vendor/simulator |
| `qfw.client.poll` | back-end client | Each result poll |
| `qfw.rpc` | DEFw | One DEFw RPC round-trip; bytes attributes; off by default |

Vendor-reported timings that did not happen under QFw's clocks (server-side
queue time, execution time) are recorded as *attributes* on
`qfw.client.execute` (`qfw.vendor.queue_time_s`, `qfw.vendor.exec_time_s`),
not as spans.

Derived by report tooling (not emitted): end-to-end latency, per-hop
breakdown, framework overhead = (`qfw.job` duration) − (back-end-reported
execution time), throughput, iteration statistics (min / max / mean / p50 /
p95).

### Context Attributes

Captured **at run time** as resource attributes (per-process facts) or span
attributes (per-run/per-job facts), because several of these change over
time. A report generated later must describe the run as it was, not as the
system looks at report time.

| Source | Attributes (namespace sketch) |
| --- | --- |
| QFw / DEFw | `service.name`, `service.version`, QFw git revision, component role (`qpm`, `qrc`, …), hostname |
| Qiskit | `qfw.qiskit.version`; transpiler settings (`qfw.transpile.optimization_level`, seed, basis gates); circuit statistics before/after (`qfw.circuit.num_qubits`, `qfw.circuit.depth`, `qfw.circuit.depth_transpiled`, gate counts, two-qubit gate count); OpenQASM payload size |
| QRMI | `qfw.qrmi.version`, resource type, `target()` payload digest, acquisition/session identifiers |
| QDMI / FoMaC | `qfw.device.name`, `qfw.device.version`, qubit count, coupling map, gate set; calibration snapshot (gate fidelities, readout errors, T1/T2 where exposed); `qfw.device.calibration_set_id` once the FoMaC Python binding exposes it — the key to knowing *which* calibration a result was obtained under |
| Vendor (e.g. IQM) | Server-side job ID, vendor-reported queue/execution times, vendor calibration identifier |
| Simulators (NWQ-Sim, TNQVM, …) | Simulator name/version, method/configuration, node and rank count, peak memory where obtainable |
| SLURM | Job ID, partition, node list, allocated resources (also scrapeable via OpenMetrics in the collector profile) |
| Environment | Container image tag/digest, Python version, key package versions (qiskit, qrmi, mqt.core, iqm-client, …) |

#### Cardinality Classes

Attributes must be classified by cardinality, because the same attribute is
cheap on a span and expensive on a metric. Time-series databases key each
series by its full label set, so a high-cardinality label multiplies stored
series; this matters concretely in the streaming profile, where
VictoriaMetrics promotes OTel resource attributes to labels automatically.
Spans have no such constraint — each span is an independent record.

Every `qfw.*` attribute carries one of two classes:

| Class | Meaning | Where it may appear |
| --- | --- | --- |
| **Dimensional** (low cardinality) | Bounded, slow-changing set of values — safe to group and filter by | Metric labels, span attributes, resource attributes |
| **Descriptive** (high cardinality) | Per-run, per-job, or free-form values | Span attributes and report JSON only — **never** metric labels |

Initial classification:

| Class | Attributes |
| --- | --- |
| Dimensional | `qfw.stack.api_path` (`native`/`qrmi`/`qdmi`/`simulator`), `qfw.device.name`, `qfw.backend.kind`, `qfw.suite.name`, `qfw.circuit.num_qubits`, `service.name`, `service.version`, status/outcome |
| Descriptive | trace and span IDs, `qfw.job.id`, vendor job IDs, SLURM job ID, `qfw.device.calibration_set_id`, calibration snapshots, coupling maps, `target()` payload digests, circuit hashes, OpenQASM payloads, package-version maps, container image digests |

Two judgment calls worth community scrutiny (see open questions):
`qfw.circuit.num_qubits` is dimensional in practice because sweeps use a
small set of sizes, but an unbounded sweep would make it descriptive; and
`qfw.device.calibration_set_id` is classed descriptive because it rotates,
even though grouping results by calibration is exactly what an analyst
wants — that grouping belongs in the report tooling and trace store, not in
metric labels.

Where a descriptive value must be reachable from a metric, record it as an
**exemplar** on the metric rather than as a label; exemplars carry a trace
ID that links an aggregate back to a representative trace.

### Metrics

Initial metric set (OTel instruments). Metric labels are drawn only from
the **dimensional** class above:

| Metric | Instrument | Purpose |
| --- | --- | --- |
| `qfw.iter.duration` | histogram | Per-iteration latency distribution for hybrid loops |
| `qfw.job.duration` | histogram | End-to-end job latency distribution under load |
| `qfw.jobs.completed` / `qfw.jobs.failed` | counter | Throughput and reliability under load |
| `qfw.rpc.duration`, `qfw.rpc.bytes` | histogram | Transport characterization (libfabric work); off by default |

### Result and Quality Data

- **Always recorded:** shot count, success/failure status, error details on
  failure, and measurement counts (inline for small results, referenced
  files above a size cutoff — see open questions).
- **Computed where feasible:** for small circuits with classically
  computable ideal distributions, a distribution-distance metric
  (e.g. Hellinger fidelity) against the ideal.
- **Suite-provided:** when running an established suite, the suite's own
  scores are captured verbatim in the report's `results` section and
  attributed to the suite (`qfw.suite.name`, `qfw.suite.version`,
  `qfw.suite.score`). QFw does not re-define them.

## Benchmark Suite Integrations

Three suites are proposed for integration. They occupy distinct layers and
are complementary, with QFw's own traces as the attribution layer
underneath all of them:

| Suite | Layer | Distinctive contribution | Integration cost |
| --- | --- | --- | --- |
| SupermarQ (Infleqtion) | Application quality (Type A) | Normalized application scores + circuit feature vectors that help *explain* cross-device differences | Lowest — an in-tree example already runs SupermarQ circuits |
| QStone (Riverlane / ORNL) | System integration under load (Type A/B, external view) | Multi-user contention, scheduler-aware three-phase jobs, user-experienced latency | Low — one `Connection` subclass or an HTTP/gRPC gateway |
| MQSS Benchmarking Framework (LRZ / MQV) | Suite orchestration (Type A) | Registry-driven runner that brings MQT Bench, QV, and RB along; alignment with the QDMI community | Low — one `DeviceAdapter` |

### SupermarQ

[SupermarQ](https://github.com/Infleqtion/client-superstaq) provides eight
application benchmarks (GHZ, Mermin-Bell, error-correction codes,
Hamiltonian simulation, QAOA and VQE proxies), each exposing `circuit()`
(Cirq or Qiskit form) and `score(counts)` — a normalized application-level
quality score — plus per-circuit feature vectors (communication, critical
depth, entanglement ratio, parallelism, liveness, measurement) that
characterize the workload.

QFw already runs SupermarQ circuits: `examples/tests/test_supermarq.py`
generates GHZ and VQE-proxy circuits and executes them through the QPM API.
It currently discards the scoring half. Completing it into a benchmark —
feeding counts back into `score()`, recording the score and feature vector
via the conventions above, and running the result across back-ends/API
paths — is the proposed **pilot**: the first end-to-end exercise of the
whole design (instrument → OTLP → extract → compare) with minimal new
integration work.

### QStone

[QStone](https://github.com/riverlane/QStone) (Riverlane, developed in
collaboration with ORNL) benchmarks the quality of the HPC–quantum
integration itself: a config defines synthetic users with probabilistic
application mixes (VQE, RB, PyMatching, QBC); QStone generates portable
scheduler job suites (SLURM sbatch, LSF, bare metal) with each application
split into `pre`/`run`/`post` phases so only `run` occupies the QPU; a
profiler aggregates per-step records into integration-level statistics
(classical vs quantum time, connection overhead, throughput per user).

QStone is the natural **load generator for Type B**: it measures what a
user population experiences under contention — turnaround, quantum/classical
split, connection overhead — from outside, while QFw's traces explain from
inside *which* hop the time went to. Running QStone workloads with QFw
instrumentation active, and correlating QStone's job IDs with QFw trace IDs,
joins the two views mechanically. QStone also directly drives the
throughput/contention rows of the Type B table.

Integration options: point QStone's HTTP or gRPC connector at a thin QFw
gateway, or implement a QFw `Connection` subclass (a three-method ABC whose
`preprocess` step hands over OpenQASM).

### MQSS Benchmarking Framework

The [MQSS Benchmarking Framework](https://github.com/Munich-Quantum-Software-Stack/MQSS-Benchmarking-Framework)
(`mqssbench`, LRZ/MQV) is a registry-driven orchestration layer for
benchmark workloads: benchmarks (quantum volume, randomized benchmarking,
QAOA, MQT Bench circuits via a provider), device adapters, analyzers, and
storage are plugins, driven by YAML configs or a Python API.

Integration is one `DeviceAdapter` (`name`, `get_backend_name`,
`validate_profiling_config`, `execute_circuit`); since QFw already presents
a Qiskit back-end (`qfw_qiskit`), the adapter is expected to be a thin
wrapper. Once it exists, every mqssbench benchmark runs through QFw against
any QPM service and any API path — and MQT Bench, QV, and RB come along for
free. The layering is: mqssbench defines and scores workloads; QFw executes
and measures them.

### Common Integration Notes

- **Correlation.** Each suite integration stamps the active QFw trace ID
  into the suite's own run records (and vice versa where the suite allows
  custom metadata), so report tooling can join suite scores with QFw traces
  without heuristics.
- **Circuit language.** SupermarQ's QASM export and QStone's core language
  are OpenQASM 2.0, while QFw's canonical form is OpenQASM 3. QFw's
  canonicalization accepts 2.0 input; raising QASM 3 support upstream in
  the suites is worthwhile but not a blocker.
- **Suggested order.** SupermarQ first (pilot; proves the measurement rig
  end to end at minimal cost), QStone second (Type B synergy and the
  ORNL collaboration), mqssbench alongside or after (cheap adapter, strong
  community-alignment value). See open questions.

## Report Generation Design

With OTel adopted, the report tooling's scope shrinks: it no longer defines
a record format, and under the collector profile many views (per-trace
waterfalls, dashboards) come from existing tools. What remains is the part
with no off-the-shelf equivalent: turning one run's telemetry plus suite
results into a **reproducible, archivable comparison artifact** — a report
you can attach to a PR, a paper, or a regression bisect.

Two tools plus a comparator (proposed home: `bin/`, see open questions),
consuming profile 1's OTLP files directly (or a collector's store under
profile 2):

```
 OTLP JSON files (per node/process)   suite results (JSON)
        │                                   │
        ▼                                   ▼
 qfw_bench_extract  ──►  run-<trace_id>.json      (canonical JSON report)
                                │
                                ▼
 qfw_bench_render   ──►  run-<trace_id>.md        (human-readable report)

 qfw_bench_compare  ──►  compare.json / compare.md   (N reports in, comparison out)
```

### Part 1: The JSON Report (Extractor)

`qfw_bench_extract --otlp <dir-or-files> [--suite-results <file>] --out <file.json>`

Responsibilities:

1. Read OTLP JSON files; assemble the span tree per trace; tolerate and
   count malformed/incomplete data (report it, don't die).
2. Attach suite results by trace-ID correlation.
3. Compute derived metrics (per-hop breakdown, end-to-end, framework
   overhead, iteration statistics from the duration histograms).
4. Validate against the report schema and write the canonical JSON report.

Report schema sketch (`report_schema: 2`):

```json
{
  "report_schema": 2,
  "conventions_version": 1,
  "run": {"trace_id": "…", "label": "iqm-via-qrmi", "started": "…", "ended": "…"},
  "workload": {"suite": "supermarq", "benchmark": "ghz", "qubits": 5, "shots": 1024,
               "features": {"communication": 0.4, "critical_depth": 0.7}},
  "stack": {
    "api_path": "qrmi",
    "qiskit": {"version": "…", "transpile": {"optimization_level": 1, "seed": 42}},
    "qrmi": {"version": "…"},
    "qdmi": null,
    "vendor": {"name": "iqm", "job_id": "…"}
  },
  "device": {"name": "…", "qubits": 20, "calibration": {"set_id": "…", "snapshot": {"…": "…"}}},
  "environment": {"image": "…", "python": "3.12.x", "packages": {"…": "…"}, "slurm": {"job_id": "…"}},
  "spans": [ {"name": "qfw.job", "start": "…", "duration_s": 4.31, "attributes": {"…": "…"}} ],
  "metrics": {
    "end_to_end_s": 4.31,
    "hops": {"serialize_s": 0.02, "qpm_queue_s": 0.4, "client_submit_s": 0.05, "backend_exec_s": 3.1},
    "framework_overhead_s": 0.74,
    "iterations": {"count": 100, "mean_s": 0.41, "p50_s": 0.39, "p95_s": 0.52}
  },
  "results": {"status": "ok", "counts_ref": "…",
              "quality": {"hellinger_fidelity": 0.94},
              "suite_scores": {"supermarq_score": 0.91}}
}
```

The `spans` array preserves the raw timeline so future tooling can compute
metrics not anticipated today without re-running anything.

### Part 2: The Human-Readable Report (Renderer)

`qfw_bench_render <run.json> [--out <file.md>]`

Renders **Markdown** from the JSON report — it displays natively on GitHub
and pastes into issues, PRs, and the wiki. The renderer never touches
telemetry files and never queries live systems; it is a pure function of
the JSON report. Layout: header (run label, workload, one-line stack
description), context tables, latency breakdown with framework overhead
called out, iteration statistics, results/quality with suite scores, and
auto-generated caveats (e.g. "calibration set ID unavailable: FoMaC binding
does not expose it yet"). Under the collector profile, interactive trace
views (e.g. Grafana's trace visualization) complement these reports.

### Comparison Reports

`qfw_bench_compare <a.json> <b.json> […] [--out compare.md]`

Takes two or more JSON reports and produces a side-by-side comparison keyed
by run label: one column per run, rows for shared metrics, per-row
best-value highlighting. Critically, the comparison also **diffs the
context** (versions, calibration set, transpiler settings) and lists what
differed — a comparison between runs whose context silently differs is the
main failure mode this tool guards against, and the capability with no
off-the-shelf equivalent.

Comparison is only defined between reports with the same `report_schema`
major version; the tool refuses otherwise rather than guessing.

## Licensing

QFw and DEFw are BSD-3-Clause. Everything this proposal adds is
permissively licensed and compatible as a dependency: OpenTelemetry SDKs,
QStone, SupermarQ, Qiskit, QRMI, and QDMI are Apache-2.0; the MQSS
Benchmarking Framework is Apache-2.0 with LLVM exception; MQT Bench is MIT.
QFw remains BSD-3-Clause; dependencies keep their own licenses. Should any
third-party source ever be vendored into the QFw tree (none is planned),
it retains its original license, headers, and any NOTICE obligations.
Copyleft-licensed suites would require case-by-case review; none of the
candidates here is copyleft.

## Implementation Phases

| Phase | Deliverable |
| --- | --- |
| 1 | OTel SDK integration in the Python components; `traceparent` propagation through DEFw RPC; file-export profile; core span vocabulary (`qfw.job` path); `qfw_bench_extract` producing schema-v2 JSON from OTLP files |
| 2 | Context attributes from Qiskit, QRMI, QDMI/FoMaC, SLURM, environment; `qfw_bench_render`; **SupermarQ pilot** — extend the in-tree example with `score()` and conventions, run across back-ends |
| 3 | `qfw_bench_compare`; hybrid-loop spans and metric histograms; **QStone integration** (QFw connector or gateway) with trace-ID correlation |
| 4 | **mqssbench `DeviceAdapter`** (brings MQT Bench, QV, RB); suite-run automation; collector-profile reference deployment (Docker Compose) with the SLURM processor; streaming-profile validation against a site pipeline (Kafka → VictoriaMetrics) |
| 5 | `qfw.rpc` spans/metrics shared with the libfabric TCP-vs-OFI work; instrumentation below the shim via the Rust (QRMI) and C (QDMI) SDKs; report archiving and longitudinal comparison |

Phase 1 alone is already useful: it produces the framework-overhead
breakdown (Type B) and the measurement rig everything else builds on.

## Open Questions and Community Input

Concrete questions where feedback is sought — plus anything not listed here:

1. **Semantic conventions** are the highest-value feedback target: are the
   span names and `qfw.*` attribute namespaces right? What context is
   missing to make cross-vendor comparisons honest? Should the conventions
   aim for a home beyond QFw?
2. **Suite integration order:** SupermarQ pilot → QStone → mqssbench is
   proposed above. Agree? Other suites (QED-C, Benchpress) that should be
   in the first wave?
3. **Quality metrics:** is a distribution-distance metric for small
   circuits worth QFw computing itself, or should quality always be
   delegated to suites?
4. **Precision:** which measurements need metric histograms (monotonic,
   distribution-bearing) rather than spans — is the split proposed in
   [Clocks, Precision, and Overhead](#clocks-precision-and-overhead)
   drawn in the right place?
5. **Cardinality classes:** is the dimensional/descriptive split in
   [Cardinality Classes](#cardinality-classes) right — particularly
   `qfw.circuit.num_qubits` (dimensional only if sweeps stay bounded) and
   `qfw.device.calibration_set_id` (classed descriptive despite grouping by
   calibration being an obvious analysis axis)? Sites running a shared
   metrics store have the most direct stake here.
6. **Streaming profile:** for sites with existing telemetry pipelines
   (e.g. ORNL's Kafka → VictoriaMetrics), is deriving span metrics via the
   spanmetrics connector the right default when no trace store is
   available, or should we expect trace stores to be deployed?
7. **Result storage:** counts inline in the JSON report vs referenced
   external files — where is the size cutoff? How long are OTLP files
   retained per run?
8. **Where should the tooling live** — `bin/`, a new top-level
   `benchmarks/` directory, or a separate repository?
9. **Upstream QASM 3:** should we push OpenQASM 3 support into QStone and
   SupermarQ, or keep accepting 2.0 at the canonicalization boundary
   indefinitely?

Please open an issue or PR against this document with suggestions.
