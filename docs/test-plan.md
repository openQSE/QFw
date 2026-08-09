# Test Plan

## Purpose

Define the system-level validation strategy for the admission, scheduler,
operation-mode, and managed-execution work described in
`docs/requirements.md`, `docs/detailed-design.md`, and
`docs/implementation-plan.md`.

This plan focuses on observable behavior across QFw, DEFw-dirsvc, installed
runtime commands, QPM, qhw-admission, qhw-scheduler, provider adapters, client
bindings, telemetry, completion queues, and operator workflows. Component and
phase-level tests from the implementation plan remain required, but they are
not repeated here except where they form system-test entry criteria.

Authentication behavior is out of scope for this milestone. QPM APIs accept
opaque `token` parameters as request metadata, but tests must not expect token
validation, caller authorization, or policy filtering based on token contents.

## Test Environment

System validation requires source-tree and installed-prefix layouts. Each
layout must support the same user-facing command surface and enough deployment
coverage to exercise these runtime modes:

- QFw-managed local runtime: `qfw-setup --profile local` starts a job-owned
  DEFw-dirsvc and one or more local QPM services from the job-local service
  manifest.
- Long-running site runtime: site-managed `qfw-dirsvc-start` and
  `qfw-service-start` processes register a long-running QPM with the
  site-scoped DEFw-dirsvc.
- Hybrid runtime: a job starts local services while also allowing discovery
  through a site-scoped directory service.
- Direct endpoint diagnostic runtime: a DEFw-wrapped QPM listens on an
  explicitly configured endpoint and is used only when the direct endpoint
  resolver scope is explicitly enabled.

The environment must provide:

- Installed or in-tree QFw and DEFw builds matching the implementation under
  test, including DEFw-dirsvc, QPM service APIs, and client proxy bindings.
- CMake install output with executable `qfw-activate`, `defw-python`,
  `qfw-setup`, `qfw-srun`, `qfw-teardown`, `qfw-dirsvc-start`, and
  `qfw-service-start` commands.
- Source-tree activation with the same logical path variables as the installed
  layout.
- Runtime profile templates for implicit production, local, and hybrid
  profiles, plus a service-runtime configuration with
  `qpm.completion-queues.retention` settings.
- qhw-admission and qhw-scheduler bindings available to the QPM controller.
- At least one deterministic execution target, preferably a simulator QPM, for
  repeatable execution, cancellation, timeout, and failure tests.
- A long-running service target that can remain up across multiple client jobs.
- Configurable admission device profiles, admission policies, estimator
  policies, scheduler policies, dispatch-depth limits, and reservation TTLs.
- A way to force capacity pressure, delayed admission, scheduler failure,
  provider failure, peer loss, service restart, and stale directory-generation
  cases without relying on production hardware instability.
- Operator-visible logs, telemetry, audit records, and reservation/task
  inspection APIs for validating state ordering and reconciliation.
- A clock-control or short-TTL mechanism for expiration tests.
- Completion-queue retention controls, including short TTL and small
  max-record settings, for deterministic eviction tests.

Hardware-backed tests may be run as a provider smoke layer after the simulator
system suite passes. Hardware unavailability must not block acceptance of
provider-independent admission, scheduler, resolver, and telemetry semantics.

## Entry Criteria

- Phase-level automated tests from `docs/implementation-plan.md` have passed
  for the implementation slice under test, including operation-mode,
  API/token-pass-through, controller scaffolding, admission integration,
  scheduler integration, telemetry, reconciliation, hardening,
  runtime-startup, and completion-queue tests.
- The target environment can start, stop, and inspect DEFw-dirsvc and QPM
  services without manual code edits.
- The target environment can activate both source-tree and installed-prefix
  layouts and run a trivial application through `defw-python`.
- Test data defines at least two reservations with different owners, jobs or
  allocations, devices or scopes, and capacity limits.
- Structured status and error envelopes are enabled on execution, admission,
  scheduler-control, and telemetry/discovery APIs.

## System Test Cases

| Test ID | Scenario | Requirements |
| --- | --- | --- |
| ST-001 | QFw-managed service discovery. Start an allocation-local DEFw-dirsvc and QPM service, verify QPM registers service records and API bindings, resolve execution and telemetry bindings, and confirm DEFw-dirsvc performs no QPM admission capacity accounting. | `OPM-001`, `OPM-003`, `DISC-001`, `DISC-002`, `DISC-004`, `API-003` |
| ST-002 | Long-running QPM discovery. Start a long-running DEFw-wrapped QPM, register it with a site-scoped DEFw-dirsvc, inject directory configuration into a client allocation, resolve and call it through DEFw RPC, and verify an unregistered direct endpoint is callable only when the direct endpoint resolver scope is explicitly enabled. | `OPM-002`, `OPM-003`, `DISC-003`, `DISC-004`, `DISC-005`, `API-003` |
| ST-003 | Multi-scope resolver policy. Present local, site-scoped, hybrid, and direct-endpoint candidates, verify scope annotation, deterministic ordering, tie-breaking, ambiguity errors, stale generation rejection, selected API binding validation, and no silent hardware-to-simulator fallback. | `DISC-004`, `DISC-005`, `API-003` |
| ST-004 | API category separation and token placeholders. Resolve and call execution, admission control, admission policy configuration, scheduler control, and telemetry/discovery bindings. Verify tokens are preserved as opaque metadata and are not parsed, validated, or used for authorization. | `CAT-001` through `CAT-007`, `API-001` through `API-004`, `CTRL-001` |
| ST-005 | Client pass-through. Submit through QFw backend, job, sampler, and estimator paths with `reservation_id`, token, timeout, and execution options. Verify context reaches QPM unchanged and production resource-affecting execution without a reservation ID is rejected. | `API-001`, `API-003`, `CAT-002` |
| ST-006 | Admission reservation workflows. Exercise `evaluate`, `reserve`, `renew`, `release`, `cancel`, `get_reservation`, and `list_reservations`. Verify accepted, delayed, and rejected outcomes include machine-readable reasons and that qhw-admission is the authoritative reservation store. | `ADM-001` through `ADM-004`, `ADM-016`, `ADM-017`, `CAT-003` |
| ST-007 | Reservation validation and capacity holds. Attempt work with invalid, expired, released, cancelled, wrong-device, wrong-scope, and over-limit reservations. Verify active matching reservations call `authorize_usage()` and `consume()` before scheduler insertion, while failed holds never enter qhw-scheduler or provider dispatch. | `ADM-005`, `ADM-006`, `ADM-018`, `ADM-019`, `API-004` |
| ST-008 | Pending-capacity workflow. Force delayed authorization and verify QPM creates pending entries without qhw-admission usage events or scheduler tasks. Release capacity and verify retry uses the same qtask ID and usage payload before `consume()` and scheduler insertion. | `ADM-019`, `ADM-020`, `ADM-022`, `SCHED-004` |
| ST-009 | Scheduled asynchronous execution. Submit admitted async work, verify QPM returns circuit and qtask handles, inserts the qtask into qhw-scheduler, dispatches only scheduler-selected work, bounds provider queue depth, and blocks normal provider-direct bypasses. | `SCHED-001`, `SCHED-003`, `SCHED-005`, `SCHED-007`, `SCHED-009`, `SCHED-010` |
| ST-010 | Synchronous execution contract. Run `sync_run()` through the same managed path as async execution. Verify terminal success, immediate timeout, non-terminal timeout that leaves work active, and `cancel_on_timeout` behavior with structured task handles and lifecycle state. | `SCHED-008`, `API-001`, `API-004`, `CAT-002` |
| ST-011 | Completion ordering and result visibility. Complete, fail, and cancel provider work. Verify QPM records actual usage or returns unused capacity, updates qhw-scheduler terminal state, and only then exposes terminal sync results, reservation-scoped completion-queue records, events, or result metadata. | `ADM-007`, `ADM-021`, `SCHED-005`, `SCHED-006`, `STATE-004` |
| ST-012 | Cancellation propagation. Cancel pending-capacity, queued, selected, submitted, running, and timeout-returned qtasks. Verify cancellation reaches QPM pending state, qhw-scheduler, provider handles when present, result/event state, and qhw-admission accounting. | `SCHED-011`, `ADM-021`, `STATE-003`, `STATE-004` |
| ST-013 | Reservation close protocol. Release, cancel, and expire reservations with pending, held, selected, and provider-running qtasks. Verify QPM marks the reservation closing, rejects new work, stops pending retries, finalizes held-task accounting while qhw-admission still reports active, and then performs the terminal lifecycle call. | `ADM-003`, `ADM-007`, `ADM-017`, `ADM-021`, `ADM-022`, `STATE-004` |
| ST-014 | Managed task status and queue observations. Poll task status through every visible state: pending capacity, queued, waiting, selected, submitted, running, completed, failed, cancelled, and timed out. Verify queue position and wait/start estimates are present only when policy and telemetry support them. | `SCHED-012`, `SCHED-013`, `SCHED-014`, `API-004`, `CTRL-008` |
| ST-015 | Telemetry and capacity snapshots. Query backend metadata, calibration/topology data, reservation state, task metadata, capacity snapshots, queue metrics, scheduler policy state, device availability, confidence, timestamps, and policy context. Verify unavailable estimates are explicit. | `API-002`, `CAT-005`, `CTRL-002` through `CTRL-008` |
| ST-016 | Liveness, restart, and reconciliation. Inject peer loss, heartbeat timeout, service deregistration, QPM restart, stale directory generation, incomplete holds, unfinished scheduler tasks, and provider handles requiring recovery. Verify directory state, resolver behavior, reconciliation faults, and audit records. | `DISC-001`, `DISC-004`, `CTRL-004`, `STATE-004` |
| ST-017 | Operation-mode parity. Run the same reservation, execution, cancellation, timeout, release, and telemetry workflows in QFw-managed mode and long-running QPM mode. Verify the externally visible QPM API semantics match after binding. | `OPM-001` through `OPM-003`, `API-003`, `CAT-007` |
| ST-018 | Compatibility debt removal. Verify legacy directory `reserve()` and `release()` capacity semantics and unmanaged public execution bypasses are unavailable in production mode, while explicitly enabled diagnostic bypasses are gated, separated from normal APIs, and audited. | `DISC-002`, `SCHED-009`, `SCHED-010`, `API-001` |
| ST-019 | Installed and source runtime parity. Build and install QFw, activate both source-tree and installed-prefix layouts, verify command executability, logical path variables, Python package imports, DEFw Python version checks, virtual-environment preservation, and absence of installed-mode Python executable rewriting. | `OPM-001`, `OPM-002`, `API-003` |
| ST-020 | Runtime profile and lifecycle ownership. Exercise implicit production, local, and hybrid profiles through `qfw-setup`, `qfw-srun`, and `qfw-teardown`. Verify local and hybrid profiles start only job-owned services, production jobs leave site services running, setup fails on directory readiness timeout, and teardown cleans only job-owned run state. | `OPM-001` through `OPM-003`, `DISC-001`, `DISC-003`, `DISC-004`, `DISC-005`, `API-003` |
| ST-021 | Service lifecycle commands. Start site directory and QPM services through `qfw-dirsvc-start` and `qfw-service-start`, verify service run directories, PID or readiness state, signal handling, directory registration before readiness, service-manager environment compatibility, and nonzero startup on registration timeout. | `OPM-001`, `OPM-002`, `DISC-001`, `DISC-003`, `DISC-005` |
| ST-022 | Reservation-scoped completion queues. Create reservation queues on accepted reservations, complete multiple tasks under multiple reservations, verify oldest-ready and targeted `peek_cq()` and `read_cq()` behavior, missing-reservation and mismatched-reservation rejection, peek idempotency, single completion consumption, and diagnostic-result separation. | `CAT-002`, `API-001`, `API-004`, `SCHED-012`, `STATE-001` through `STATE-003` |
| ST-023 | Completion notifications and retention. Verify terminal completions are enqueued after admission and scheduler finalization and before event dispatch, notifications do not consume polling records, QRC completion sink ownership is acknowledged only after enqueue, retention settings evict records deterministically, no-longer-retained responses are structured, and terminal reservation queues are garbage-collected only after active work and retention conditions are satisfied. | `ADM-021`, `SCHED-005`, `SCHED-006`, `SCHED-011`, `STATE-003`, `STATE-004` |
| ST-024 | Long-running QPM concurrent app waves. Allocate at least three nodes, start a site DEFw-dirsvc, PRTE DVM, and long-running `nwqsim` QPM on one service node, then launch two or more application instances concurrently on separate app nodes through site-scoped `qfw-setup`, `qfw-srun`, and `qfw-teardown`. Repeat a second concurrent wave against the same QPM without restarting the service plane. Verify each app resolves the site QPM, reserves capacity, submits work, receives completion, releases the reservation, deregisters or disconnects cleanly, and that app teardown does not stop the site directory, DVM, or QPM. | `OPM-002`, `OPM-003`, `DISC-003`, `DISC-004`, `DISC-005`, `API-003`, `CAT-002`, `ADM-001`, `ADM-003`, `SCHED-008`, `STATE-003` |

## Workflow Checks

- For each operation mode, perform a full happy-path workflow: resolve QPM,
  configure admission and scheduler policy, create a reservation, submit async
  work, observe queued and running state, receive completion, inspect usage,
  release the reservation, and inspect final telemetry.
- Run the normal user lifecycle in each runtime profile: source or activate
  QFw, run `qfw-setup`, launch a trivial application with `qfw-srun`, and run
  `qfw-teardown`.
- Run the site service lifecycle separately from the user job lifecycle:
  start site services with `qfw-dirsvc-start` and `qfw-service-start`, then
  confirm user teardown does not stop those site-owned services.
- Run the long-running QPM concurrency workflow from a three-node allocation:
  keep the site directory, PRTE DVM, and `nwqsim` QPM running on the service
  node while two application nodes execute simultaneous app waves through
  site-scoped runtime state.
- Repeat the happy path with `sync_run()` and verify timeout behavior does not
  create a second execution path.
- Run capacity-pressure workflows where one reservation consumes its allowed
  capacity and another qtask is delayed, rejected, or held pending according to
  site policy.
- Run failure workflows for invalid reservation, expired reservation, scheduler
  failure, provider failure, provider completion racing cancellation, and QPM
  restart while work is active.
- Compare reservation, qtask, circuit, scheduler task, provider handle, usage,
  event, and result identifiers across admission inspection, scheduler
  inspection, task status, completion queues, telemetry, and audit output.
- Read and peek completion queues by reservation with and without `cid`
  selectors, then confirm events remain independent from polling and retention
  produces structured no-longer-retained results.
- Confirm compatibility wrappers, if still present during transition, route
  through the managed reservation-scoped controller path or return a structured
  unsupported/diagnostic-only outcome.

## Manual Checks

- Inspect the DEFw-dirsvc directory view in both operation modes and confirm
  service records contain stable service identity, runtime identity, generation,
  endpoint metadata, selector metadata, and concrete API bindings.
- Inspect QPM logs for each managed execution and confirm provider submission
  occurs only after reservation validation, accepted capacity hold, scheduler
  insertion, and scheduler selection.
- Inspect qhw-admission state after completion, cancellation, release, cancel,
  and expiration to confirm usage accounting is final before terminal results
  are visible.
- Inspect qhw-scheduler state during queue pressure and provider failure to
  confirm task lifecycle transitions match QPM-visible task status.
- Inspect telemetry and audit records for registration, deregistration, peer
  loss, restart, generation change, policy change, diagnostic bypass,
  reconciliation fault, and reservation close events.
- Review direct endpoint fallback and diagnostic bypass configuration to
  confirm both require explicit enablement and cannot be selected accidentally
  in production workflows.
- Inspect the installed prefix and confirm commands, private helpers, service
  modules, service API bindings, Python packages, examples, and configuration
  templates are installed under the expected package-owned locations.
- Inspect activation output and confirm `qfw-activate` exports logical QFw and
  DEFw path variables without starting services or replacing the user's Python
  executable.
- Inspect site-owned configuration and confirm privileged service-runtime and
  device-access files live outside the software prefix and are not modified by
  normal user jobs.
- Inspect long-running QPM evidence after the concurrent wave test: the site
  directory log must show service registration before the first app starts,
  the QPM log must show requests from multiple app instances, app runtime
  state must not contain `QFW_LOCAL_DIRSVC_ENDPOINT`, and the QPM process must
  remain alive after each app-side `qfw-teardown`.
- Inspect service-runtime configuration and confirm
  `qpm.completion-queues.retention` is loaded by QPM, invalid explicit values
  fail readiness, and `QFW_QPM_COMPLETION_*` overrides are limited to tests or
  emergency service operation.
- Confirm operator documentation or runbook notes identify which command-line
  options, environment variables, and site or runtime files select local
  directories, site-scoped directories, hybrid lookup, and direct fallback
  endpoints.

## Acceptance Criteria

- All ST-001 through ST-024 scenarios pass in the simulator or deterministic
  test target environment. Provider-specific hardware smoke tests may be
  reported separately when hardware is unavailable.
- Every requirement group in `docs/requirements.md` has at least one passing
  system scenario or a justified lower-level test with equivalent coverage.
- QFw-managed and long-running modes expose the same reservation, release,
  execution, status, cancellation, result, and telemetry semantics after QPM
  binding.
- Source-tree and installed-prefix layouts expose the same public command
  surface and runtime profile behavior.
- User job lifecycle commands never stop site-owned services, and service
  lifecycle commands do not depend on the user job lifecycle.
- Long-running QPM evidence includes at least two concurrent app instances and
  at least two app waves against one continuously running site QPM service.
- Normal resource-affecting execution requires an active reservation, an
  accepted qhw-admission capacity hold, qhw-scheduler insertion, and scheduler
  selection before provider submission.
- Pending-capacity work never has a scheduler task or qhw-admission usage event
  before an accepted hold is committed.
- Terminal task results, completion events, and completion-queue records are
  not visible until qhw-admission accounting and qhw-scheduler terminal state
  have been updated.
- Managed completion polling requires a reservation ID, scopes optional
  circuit selectors to that reservation, keeps notifications independent from
  polling, and applies bounded retention with structured eviction outcomes.
- Release, cancel, and expiration close reservations only after pending work
  and held work have been finalized or an operator-visible reconciliation fault
  has been emitted.
- Structured outcomes distinguish invalid reservation, insufficient allowance,
  pending capacity, policy-delayed work, cancelled work, expired reservation,
  timeout, scheduler failure, provider failure, and diagnostic-bypass outcomes.
- Telemetry exposes capacity, queue, scheduler policy, task lifecycle,
  reservation lifecycle, estimate confidence, timestamps, and policy context
  consistently with the managed-resource lifecycle.
- Legacy DEFw directory reservation behavior and unmanaged public execution
  bypasses are absent from production workflows.
- Test evidence includes command logs or CI artifacts, relevant service logs,
  telemetry snapshots, and any documented deviations or environment skips.
