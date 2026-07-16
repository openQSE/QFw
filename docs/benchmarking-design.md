# QFw Benchmarking Design (Proposal)

Status: **draft — request for community input**

This document proposes adding benchmarking support to QFw. It is intended as
a starting point for discussion: the measurement lists in particular are
initial proposals, and community members are encouraged to suggest additions,
removals, or changes. See [Open Questions and Community Input](#open-questions-and-community-input).

## Table Of Contents

- [Motivation](#motivation)
- [Two Kinds of Benchmarking](#two-kinds-of-benchmarking)
- [Design Principles](#design-principles)
- [Measurement Capture Design](#measurement-capture-design)
  - [Benchmark Record Format](#benchmark-record-format)
  - [Logging Integration](#logging-integration)
  - [Instrumentation Points](#instrumentation-points)
- [Initial Capture List](#initial-capture-list)
  - [Timing Events Added to QFw](#timing-events-added-to-qfw)
  - [Context Pulled from Other Subsystems](#context-pulled-from-other-subsystems)
  - [Result and Quality Metrics](#result-and-quality-metrics)
- [Report Generation Design](#report-generation-design)
  - [Part 1: The JSON Report (Extractor)](#part-1-the-json-report-extractor)
  - [Part 2: The Human-Readable Report (Renderer)](#part-2-the-human-readable-report-renderer)
  - [Comparison Reports](#comparison-reports)
- [Toward an Automated Benchmark Suite](#toward-an-automated-benchmark-suite)
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

This proposal covers both, with a shared measurement and reporting
infrastructure.

QFw's value here is **not** defining new benchmark circuits or metrics:
established suites already exist (MQT Bench, QED-C Application-Oriented
Benchmarks, Qiskit Benchpress, SupermarQ). QFw's value is being the neutral
**execution harness and measurement rig** that can run such suites across
back-ends and API paths, and attach consistent context (device calibration,
software versions, timing breakdowns) to every result.

## Two Kinds of Benchmarking

### Type A: Benchmarking *through* QFw (QFw as testbed)

The same workload is executed against different back-ends or through
different software paths, and the results are compared.

| Comparison axis | What varies | Example question |
| --- | --- | --- |
| Vendor / device | The QPU or simulator behind the QPM service | How does device X compare to device Y on the QED-C suite? |
| Resource-management API | Native vendor client vs QRMI vs QDMI, same device | What overhead and behavioral differences does each API layer introduce? |
| Library / compiler | Front-end or transpiler settings, same abstract circuit | Which toolchain produces shallower circuits for this ISA, and at what compile-time cost? |
| Simulator scaling | Simulator choice, node/rank count, circuit size | How do NWQ-Sim and TNQVM scale with qubits, depth, and nodes on this cluster? |

The API-path comparison is worth highlighting: because QFw can reach the same
IQM hardware through the native IQM client path, through QRMI, and through
QDMI, it can isolate the cost and behavior of the resource-management layer
itself — data directly relevant to ongoing interface-convergence discussions.

### Type B: Benchmarking *of* QFw (framework overhead)

QFw's own contribution to end-to-end latency and throughput is measured.

| Measurement | Why it matters |
| --- | --- |
| Orchestration latency per hop (submit → QPM → QRC → back-end client) | Locates where time goes inside the framework |
| Serialization / canonicalization cost (circuit → OpenQASM3) | Fixed per-job overhead |
| Hybrid-loop round-trip latency | For VQE/QAOA, per-iteration framework latency often dominates user-visible runtime |
| Job throughput under load | Concurrent jobs, batching behavior, scheduler interaction |
| DEFw RPC / transport cost | Baseline for the libfabric (TCP vs OFI) transport work, which will consume this same instrumentation |

Both types share the same record format, logging path, and report tooling.
Type B events are a subset of what Type A runs also: a vendor
comparison report automatically includes the framework-overhead breakdown.

## Design Principles

1. **Structured records, not prose.** Measurements are emitted as
   single-line, machine-parseable records with a fixed sentinel — never as
   free-form log text to be regexed later. Human-facing log wording can
   change freely without breaking reports.
2. **Capture time-varying context at run time.** Anything that could differ
   between execution time and report time (device calibration, transpiler
   settings, queue state) is recorded when the run happens. The report
   script *aggregates and formats*; it never *discovers* anything that could
   have changed since the run. Static facts (package versions, topology) may
   be gathered either way, but run-time capture is preferred for uniformity.
3. **JSON first, human-readable second.** The canonical report is a
   machine-readable JSON document; the human-readable report is rendered
   from it. Comparing runs means diffing structured reports, not parsing
   formatted text.
4. **Low perturbation.** Instrumentation must not meaningfully disturb what
   it measures: dedicated log level, node-local log files, monotonic-clock
   durations carried in the record payload (see
   [Logging Integration](#logging-integration)).
5. **Schema-versioned everything.** Both the log record and the JSON report
   carry a schema version so old data remains parseable as formats evolve.
6. **Reuse existing suites.** Circuit sets and quality metrics come from
   established benchmark suites where possible; QFw provides execution,
   timing, and context.

## Measurement Capture Design

### Benchmark Record Format

Each measurement is one line in the DEFw log: a fixed sentinel `QFW_BENCH`
followed by a JSON object.

```
QFW_BENCH {"schema": 1, "run_id": "c3f9…", "event": "qrc.dispatch", "component": "svc_iqm_qpm", "t_wall": "2026-07-15T18:22:31.481Z", "t_mono": 10254.113724, "dur_s": 0.0132, "iter": 7, "data": {...}}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `schema` | yes | Record schema version (integer, starts at 1) |
| `run_id` | yes | Correlation ID for the benchmark run; generated at submission and propagated through every hop |
| `event` | yes | Dotted event name from the vocabulary below |
| `component` | yes | Emitting component (e.g. `qfw_qiskit`, `qpm`, `qrc`, `svc_nwqsim_qpm`) |
| `t_wall` | yes | Wall-clock timestamp (UTC, ISO 8601) — for coarse cross-node ordering only |
| `t_mono` | yes | Monotonic clock reading in the emitting process — for intra-process interval math |
| `dur_s` | no | Duration in seconds for span-style events, measured with the monotonic clock around the work |
| `iter` | no | Iteration index for hybrid-loop workloads |
| `data` | no | Event-specific payload (context snapshots, sizes, counts) |

Rules:

- **Durations are computed inside one process** with the monotonic clock.
  Wall-clock timestamps from different nodes are never subtracted (clock
  skew across nodes exceeds the intervals of interest).
- The `run_id` is minted where the workload enters QFw (the `qfw_qiskit`
  back-end or the QPM API) and travels with the job through every service.
- Records must be single-line JSON so extraction is `grep QFW_BENCH | parse`.

### Logging Integration

DEFw's logging (Python stdlib `logging`, synchronous `FileHandler`, custom
DEFw levels — see `DEFw/python/infra/defw_common_def.py`) is sufficient for
the expected record rates. Analysis: benchmark instrumentation emits tens of
records per job (order 10 per iteration for hybrid loops with ≥100 ms
iterations); at tens of microseconds per synchronous log call, perturbation
is well below run-to-run noise. No logging rework is required up front.
Three deployment rules keep it that way:

1. **Dedicated `DEFW_BENCH` log level.** Added via the existing
   `add_logging_level()` mechanism, so benchmark records can be enabled
   *independently* of verbose diagnostic levels. Benchmarking with
   `DEFW_RPC`-style per-message tracing enabled would perturb results; the
   dedicated level makes "bench on, debug off" the natural configuration.
2. **Node-local log files.** The DEFw log path follows the `defw: tmp:`
   configuration; benchmark deployments must point it at node-local storage
   (e.g. `/tmp`), never a shared/NFS filesystem, where write latency is
   large and jittery.
3. **Timings ride in the payload.** Because durations are measured with the
   monotonic clock around the work and merely *reported* via the record,
   logging cost can delay the program slightly but cannot corrupt the
   measured values.

If a future workload needs record rates beyond what synchronous logging
sustains (e.g. per-message transport tracing for the libfabric work), the
contained upgrades are: a bench-specific handler without the
`funcName`/`lineno` format fields (which force a stack walk per record), and
a `QueueHandler`/`QueueListener` pair moving formatting and I/O off the hot
thread. Nothing in this design blocks either.

### Instrumentation Points

Instrumentation follows the job path through the stack:

```
 user program
     │
 qfw_qiskit back-end          app.*   events
     │
 QPM API / QPM service        qpm.*   events
     │
 QRC                          qrc.*   events
     │
 back-end client              client.* events
 (IQM client | QRMI | QDMI | simulator)
     │
 device / simulator
```

Each hop emits paired span events (or one event with `dur_s`) so the report
can build a per-hop latency breakdown and compute framework overhead as
(end-to-end time) − (back-end-reported execution time).

## Initial Capture List

**This is the section where community input is most wanted.** The lists
below are a starting proposal, split into (a) timing events QFw will emit,
(b) context pulled from surrounding subsystems, and (c) result/quality
metrics.

### Timing Events Added to QFw

| Event | Emitted by | Meaning / duration measured |
| --- | --- | --- |
| `bench.run.start` / `bench.run.end` | front-end | Brackets the whole benchmark run; `data` carries the run label and workload description |
| `app.submit` | `qfw_qiskit` | User program hands circuits to the QFw back-end |
| `app.serialize` | `qfw_qiskit` | Circuit → canonical OpenQASM3 conversion (`dur_s`, plus payload size in `data`) |
| `app.result` | `qfw_qiskit` | Results delivered back to the user program (end of end-to-end span) |
| `qpm.job.received` | QPM service | Job arrives at the QPM |
| `qpm.transpile` | QPM service | QFw-side transpilation, if any (`dur_s`; pre/post circuit stats in `data`) |
| `qpm.queue` | QPM service | Time spent queued inside QFw before dispatch (`dur_s`) |
| `qrc.dispatch` | QRC | Hand-off to the back-end client (`dur_s` for the dispatch call) |
| `client.acquire` | back-end client | Resource/session acquisition: QRMI `acquire`, QDMI session open, or vendor-client connect (`dur_s`) |
| `client.submit` | back-end client | Job submission call to the vendor/simulator (`dur_s` of the call itself) |
| `client.poll` | back-end client | Each result poll (`dur_s`; poll count derivable) |
| `client.result` | back-end client | Results received from vendor/simulator; `data` carries vendor-reported timing where available |
| `rpc.call` | DEFw | DEFw RPC round-trip (`dur_s`, bytes in `data`) — off by default; enables transport benchmarking (libfabric TCP vs OFI) |
| `iter.start` / `iter.end` | front-end | Brackets one hybrid-algorithm iteration (`iter` index set on all records in between) |

Derived (computed by the report script, not logged): end-to-end latency,
per-hop breakdown, framework overhead, in-QFw queue wait, throughput
(jobs/unit time), and per-iteration statistics (min / max / mean / p50 /
p95).

### Context Pulled from Other Subsystems

Captured **at run time** and emitted as `data` payloads on context events
(`ctx.qiskit`, `ctx.qrmi`, `ctx.qdmi`, `ctx.backend`, `ctx.env`), because
several of these change over time:

| Source | What to capture |
| --- | --- |
| Qiskit | Version; back-end name; transpiler settings (`optimization_level`, seed, basis gates); circuit statistics before and after transpilation (qubit count, depth, total gate count, two-qubit gate count); OpenQASM3 payload size |
| QRMI | QRMI version; resource type; `target()` payload (device configuration/properties as served to the client); acquisition/session identifiers |
| QDMI / FoMaC | Device name and version; qubit count; coupling map; supported gate set; calibration snapshot as exposed by FoMaC (gate fidelities, readout errors, T1/T2 where available); calibration set ID once the FoMaC Python binding exposes it — this is the key to knowing *which* calibration a result was obtained under |
| Vendor (e.g. IQM) | Server-side job ID; vendor-reported queue time and execution time; any vendor-reported calibration identifier |
| Simulators (NWQ-Sim, TNQVM, …) | Simulator name and version; method/configuration; node and rank count; peak memory where obtainable |
| SLURM | Job ID; partition; node list; allocated resources |
| Environment | Container image tag/digest; Python version; versions of key packages (qiskit, qrmi, mqt.core, iqm-client, …); hostname per component; QFw git revision |

Rationale for run-time capture: a report script that queries QDMI hours
after a run may see a *different* calibration than the one in effect during
execution, and the report would silently misattribute results. The report
script therefore only reads what the run recorded.

### Result and Quality Metrics

For Type A comparisons, timing alone is not enough — result quality must be
recorded. Proposal:

- **Always recorded:** measurement counts (or a reference to where they are
  stored, for large results), shot count, success/failure status, error
  details on failure.
- **Computed where feasible:** for small circuits with classically
  computable ideal distributions, a distribution-distance metric
  (e.g. Hellinger fidelity) against the ideal.
- **Suite-provided:** when running an established suite (MQT Bench, QED-C,
  Benchpress), the suite's own scores are captured verbatim in the report's
  `results` section and attributed to the suite. QFw does not re-define
  them.

## Report Generation Design

Two scripts (proposed home: `bin/` alongside existing tooling, or a new
`benchmarks/` directory — see open questions), sharing a small library:

```
 defw_py.log files (one per node/agent)
        │
        ▼
 qfw_bench_extract  ──►  run-<run_id>.json      (canonical JSON report)
                                │
                                ▼
 qfw_bench_render   ──►  run-<run_id>.md        (human-readable report)

 qfw_bench_compare  ──►  compare.json / compare.md   (N reports in, comparison out)
```

### Part 1: The JSON Report (Extractor)

`qfw_bench_extract --logs <dir-or-files> [--run-id <id>] --out <file.json>`

Responsibilities:

1. Scan the given log files for `QFW_BENCH` lines; parse the JSON payloads;
   tolerate and count malformed lines (report them, don't die).
2. Group records by `run_id`; stitch a per-run timeline across the log files
   of different nodes/services using `t_wall` for ordering and per-process
   `t_mono` for interval math.
3. Compute the derived metrics (per-hop breakdown, end-to-end, overhead,
   iteration statistics).
4. Validate against the report schema and write the canonical JSON report.

Report schema sketch (`report_schema: 1`):

```json
{
  "report_schema": 1,
  "run": {"run_id": "…", "label": "iqm-via-qrmi", "started": "…", "ended": "…"},
  "workload": {"suite": "mqt-bench", "circuit": "ghz", "qubits": 5, "shots": 1024, "iterations": null},
  "stack": {
    "path": "qrmi",
    "qiskit": {"version": "…", "transpile": {"optimization_level": 1, "seed": 42}},
    "qrmi": {"version": "…", "target": {"…": "…"}},
    "qdmi": null,
    "vendor": {"name": "iqm", "job_id": "…"}
  },
  "device": {"name": "…", "qubits": 20, "calibration": {"set_id": "…", "snapshot": {"…": "…"}}},
  "environment": {"image": "…", "python": "3.12.x", "packages": {"…": "…"}, "slurm": {"job_id": "…"}},
  "events": [ {"event": "app.submit", "component": "qfw_qiskit", "…": "…"} ],
  "metrics": {
    "end_to_end_s": 4.31,
    "hops": {"serialize_s": 0.02, "qpm_queue_s": 0.4, "client_submit_s": 0.05, "backend_exec_s": 3.1, "…": 0},
    "framework_overhead_s": 0.74,
    "iterations": {"count": 100, "mean_s": 0.41, "p50_s": 0.39, "p95_s": 0.52}
  },
  "results": {"status": "ok", "counts_ref": "…", "quality": {"hellinger_fidelity": 0.94}, "suite_scores": {}}
}
```

The `events` array preserves the raw timeline so future tooling can compute
metrics not anticipated today without re-running anything.

### Part 2: The Human-Readable Report (Renderer)

`qfw_bench_render <run.json> [--out <file.md>]`

Renders **Markdown** from the JSON report — chosen because it displays
natively on GitHub and can be pasted into issues, PRs, and the wiki. The
renderer never touches log files and never queries live systems; it is a
pure function of the JSON report. Layout:

1. **Header** — run label, date, workload, one-line stack description
   ("GHZ-5, 1024 shots, IQM via QRMI, calibration set …").
2. **Context tables** — software versions, device snapshot summary,
   environment.
3. **Latency breakdown** — per-hop table plus a simple textual bar view;
   framework overhead called out explicitly.
4. **Iteration statistics** (hybrid runs) — count, mean, p50, p95, worst.
5. **Results / quality** — status, quality metrics, suite scores.
6. **Caveats** — auto-generated notes (e.g. "N malformed records skipped",
   "calibration set ID unavailable: FoMaC binding does not expose it yet").

Other output formats (HTML, CSV extracts) can be added later as alternative
renderers over the same JSON.

### Comparison Reports

`qfw_bench_compare <a.json> <b.json> […] [--out compare.md]`

Takes two or more JSON reports and produces a side-by-side comparison keyed
by run label: one column per run, rows for the shared metrics (latency
breakdown, overhead, iteration stats, quality scores), with per-row
best-value highlighting. The comparison also diffs the *context* (versions,
calibration set, transpiler settings) and lists what differed — a comparison
between runs whose context silently differs in an unnoticed way is the main
failure mode this tool must guard against.

Comparison is only defined between reports with the same `report_schema`
major version; the tool refuses otherwise rather than guessing.

## Toward an Automated Benchmark Suite

The pieces above compose into automation without further design changes:

1. A **benchmark scenario** is a script that launches a workload through QFw
   with a chosen stack path and a run label (e.g.
   `ghz5 --path native|qrmi|qdmi`, or an MQT Bench / QED-C driver).
2. A **suite run** executes a set of scenarios (optionally as SLURM jobs),
   then runs `qfw_bench_extract` per run and `qfw_bench_compare` across
   them.
3. Reports (JSON + Markdown) are archived per suite run; comparing *suite
   runs over time* is just `qfw_bench_compare` across archives — this is
   how regressions in QFw itself get caught, and how the libfabric TCP→OFI
   transition will be quantified.

CI integration (e.g. a nightly suite against simulators, hardware runs on
demand) is a natural later step once the scenario set stabilizes.

## Implementation Phases

| Phase | Deliverable |
| --- | --- |
| 1 | `DEFW_BENCH` log level; record format + emit helper; instrumentation of the core submit path (`app.*`, `qpm.*`, `qrc.*`, `client.*`); `qfw_bench_extract` producing schema-v1 JSON |
| 2 | Run-time context capture (`ctx.*` events) from Qiskit, QRMI, QDMI/FoMaC, SLURM, environment; `qfw_bench_render` |
| 3 | `qfw_bench_compare`; hybrid-loop (`iter.*`) instrumentation; quality metrics for small circuits |
| 4 | Scenario drivers for one established suite (MQT Bench or QED-C) and the native/QRMI/QDMI path comparison; suite-run automation |
| 5 | `rpc.call` transport instrumentation shared with the libfabric work; report archiving and longitudinal comparison |

Phase 1 alone is already useful: it produces the framework-overhead
breakdown (Type B) and the measurement rig everything else builds on.

## Open Questions and Community Input

Concrete questions where feedback is sought — plus anything not listed here:

1. **What else should be captured?** Additions to the
   [Initial Capture List](#initial-capture-list) are the most valuable form
   of feedback — especially context fields needed to make cross-vendor
   comparisons honest, and metrics from suites you already use.
2. **Which established suite should be integrated first** — MQT Bench,
   QED-C Application-Oriented Benchmarks, Benchpress, other?
3. **Quality metrics:** is a distribution-distance metric for small
   circuits worth QFw computing itself, or should quality always be
   delegated to suites?
4. **Where should the tooling live** — `bin/`, a new top-level
   `benchmarks/` directory, or a separate repository?
5. **Result storage:** counts inline in the JSON report vs referenced
   external files — where is the size cutoff?
6. **Naming:** is the `QFW_BENCH` sentinel / `qfw_bench_*` tool naming
   agreeable?

Please open an issue or PR against this document with suggestions.
