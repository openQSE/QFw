# Bugs

## Target QPM Service API Categories

Status: working implementation contract

This section records the category API surface agreed during review. It is the
cross-category reference for the cleanup items below. Update this list whenever
an API decision changes so implementation does not revert to the monolithic
`api_qpm.QPM` surface or leave duplicate compatibility methods behind.

All categories are separate DEFw client bindings for the same logical QPM
service. The QPM service process and QPMController remain the implementation
owners behind those bindings. Category placement describes caller role,
authorization, and workflow semantics; it does not require separate QPM
processes.

Each category must also own its API declarations in a separate
`service-apis/` directory. A category package must not import its remote class
from a central `api_qpm` package. The target source layout is:

```text
service-apis/
  api_qpm_execution/
    __init__.py
    api_qpm_execution.py
  api_qpm_admission_control/
    __init__.py
    api_qpm_admission_control.py
  api_qpm_admission_policy_config/
    __init__.py
    api_qpm_admission_policy_config.py
  api_qpm_scheduler_control/
    __init__.py
    api_qpm_scheduler_control.py
  api_qpm_telemetry/
    __init__.py
    api_qpm_telemetry.py
  api_qpm_control/
    __init__.py
    api_qpm_control.py
```

Each package exports only its own remote API class and DEFw service-API
metadata. Shared immutable types or a common `BaseRemote` helper may live in a
dedicated common module, but remote method declarations must remain in the
category that owns them.

There is no backward-compatibility requirement for the old aggregate
`api_qpm.QPM` class, imports from `api_qpm`, duplicate aliases, or old
module/class binding names. Remove those surfaces instead of retaining
wrappers. One QPM service can still advertise all category bindings under one
logical `service_id` and route them to the same server-side QPM implementation.

### `api_qpm_execution`

Application and runtime operations for reservation-scoped managed tasks:

- `sync_run(info, reservation_id, token, timeout, cancel_on_timeout)`
- `async_run(info, reservation_id, token, timeout, cancel_on_timeout)`
- `cancel_task(task_id, reservation_id, token, reason)`
- `task_status(task_id, reservation_id, token)`
- `get_task_timing(token, reservation_id, task_id)`
- `get_task_metadata(token, reservation_id, task_id)`
- `read_cq(task_id, reservation_id, token)`
- `peek_cq(task_id, reservation_id, token)`
- `register_event_notification(endpoint, event_type, class_id, token,
  reservation_id, filters)`
- `delete_circuit(circuit_id, reservation_id, token)`

The execution category does not contain diagnostic bypass, service readiness,
service shutdown, admission configuration, or scheduler control operations.
The `read_cq()` and `peek_cq()` names remain accepted for now; a later review
may replace them with explicit result-operation names.

### `api_qpm_admission_control`

Trusted launcher and workflow-manager operations for reservation lifecycle:

- `evaluate(token, request)`
- `reserve(token, request)`
- `renew(token, reservation_id, request)`
- `release(token, reservation_id, reason)`
- `cancel(token, reservation_id, reason)`
- `get_reservation(token, reservation_id)`
- `list_reservations(token, filters)`

Pending-capacity retry is internal QPMController behavior and is not a remote
admission-control operation.

### `api_qpm_admission_policy_config`

Operator operations for device and admission-policy configuration:

- `configure_device_profile(token, device_id, profile)`
- `get_device_profile(token, device_id)`
- `set_admission_policy(token, device_id, configuration)`
- `get_admission_policy(token, device_id)`

The admission-policy configuration is one atomic structure containing policy,
estimator, baseline, policy-specific capacity, and options. Separate capacity,
estimator, and alternate admission-policy configuration APIs are removed.

### `api_qpm_scheduler_control`

Operator operations for scheduling policy and execution-target control:

- `configure_scheduler_policy(token, device_id, configuration)`
- `get_scheduler_policy(token, device_id)`
- `get_scheduler_status(token, device_id)`
- `pause_execution_target(token, device_id, reason)`
- `resume_execution_target(token, device_id)`
- `drain_execution_target(token, device_id, mode, timeout_s)`
- `configure_dispatch_limits(token, device_id, limits)`
- `get_scheduler_queue_state(token, device_id, include_restricted)`

`configure_dispatch_limits()` initially accepts `max_inflight`.
`max_provider_queue_depth` remains a device-profile capability enforced by
QPMController.

### `api_qpm_telemetry`

Read-only device, provider, capacity, queue, and lifecycle information:

- `get_backend_info(lib, token)`
- `get_device_info(lib, token)`
- `get_dynamic_backend_info(calibration_set_id, lib, token)`
- `get_calibration_snapshot(calibration_set_id, lib, token)`
- `get_coupling_graph(calibration_set_id, lib, token)`
- `get_telemetry_access_model(token)`
- `get_capacity_snapshot(token, device_id, scope_id, access_class)`
- `get_queue_metrics(token, device_id, access_class)`
- `get_service_lifecycle_telemetry(token, access_class)`

`get_dynamic_backend_info()` remains provisional until its result is defined as
distinct from calibration and device information. Telemetry contains no
mutating operation and no task-specific "last job" lookup.

### `api_qpm_control`

Privileged QPM process and internal runtime lifecycle operations:

- `test(token)` performs a lightweight RPC/process liveness check and does not
  submit provider work.
- `is_ready(token)` reports whether QPM initialization completed and the
  service can accept requests.
- `get_service_status(token)` returns structured QPM lifecycle, readiness,
  directory-registration, provider, active-reservation, and active-task state.
- `reconcile_runtime_state(token, reason)` audits and repairs inconsistent QPM
  runtime, admission-hold, scheduler-task, and provider-handle state.
- `shutdown(token, mode, timeout_s, reason)` performs an authorized graceful or
  cancelling shutdown and returns an acknowledgement before process exit.

This category is restricted to service owners and authorized site/operator
tooling. Application cleanup must not invoke it. Scheduler target pause and
drain remain in `api_qpm_scheduler_control`; read-only lifecycle history remains
in `api_qpm_telemetry`.

## Qiskit Backend Shutdown Can Stop Long-Running QPMs

Status: resolved

Observed: 2026-08-11 during the real IQM chemistry smoke workflow.

The chemistry run did not send a `shutdown` RPC to the IQM QPM in the latest
successful one-shot test, but that was because the chemistry script currently
calls `puccd_finalize()` only on the exception path. This avoids shutdown on a
successful run by accident and is not the correct ownership model.

Current source behavior still allows application cleanup to stop a long-running
QPM:

- `QFwEstimatorV2.shutdown()` calls `self._backend.shutdown()`.
- `QFwBackend.shutdown()` calls `self.qpm.shutdown()`.
- Chemistry `puccd_finalize()` calls `qfw_estimator.shutdown()` when QFw is in
  use.

Impact: if chemistry cleanup is restored to a normal `finally` path, or if any
other application calls `QFwEstimatorV2.shutdown()`, the application can shut
down a site-owned or long-running QPM. That violates the intended lifecycle
boundary where service teardown is owned by `qfw-teardown`,
`qfw_iqm_site_services.sh stop`, or an operator/site-driver path.

Expected behavior:

- Application/backend cleanup may release app-owned resources, release
  app-owned reservations, dump local metrics, and disconnect the local DEFw
  Python endpoint.
- Application/backend cleanup must not call remote `qpm.shutdown()` for a
  long-running or site-owned QPM.
- Explicit QPM shutdown should remain a service lifecycle operation controlled
  by runtime teardown or site/operator tooling.

Evidence from the last one-shot chemistry run:

- Run directory:
  `/workspace/qfw-container-base/qfw-runs/chem-iqm-site-20260811-053152`
- Exact QPM service log counts showed `shutdown_rpc=0`, while circuit execution
  proceeded through `async_run`, IQM submission, and measurement retrieval.
- The absence of `shutdown_rpc` confirms the run avoided the shutdown path; it
  does not prove the source-level lifecycle bug is fixed.

Proposed fix:

- Split qiskit backend cleanup from service shutdown.
- Make `QFwBackend.shutdown()` or a replacement close only local/app resources
  by default.
- Move intentional service shutdown to the privileged `api_qpm_control`
  binding, gated to local test workflows or service-owner tooling.
- Update chemistry cleanup to use a normal finalize path once backend cleanup no
  longer stops the QPM.

Resolution:

- Qiskit backend shutdown now releases only application-local runtime state.
- QPM lookup probe failures no longer shut down the resolved remote service.
- Explicit service termination is tracked under the privileged
  `api_qpm_control` implementation item below.

## Unused Diagnostic Execution Bypass APIs

Status: resolved

The QPM execution API exposes `diagnostic_sync_run()` and
`diagnostic_async_run()`. No production backend, application, example, setup
path, or operator workflow calls either method. `diagnostic_async_run()` is
used only by mock tests that exercise the bypass implementation, while
`diagnostic_sync_run()` has no runtime caller.

These methods bypass the normal reservation, admission, and scheduler path.
They were added as a restricted diagnostic path related to `SCHED-010`, but
that requirement only constrains a bypass when one exists; it does not require
QFw to provide one. Keeping an unused bypass increases the public API and
authorization surface without supporting a required workflow.

Expected behavior:

- All public QPM execution requests go through reservation validation,
  admission control, and scheduler selection.
- QPM does not expose a remote execution API that bypasses the managed path.
- Diagnostics observe managed execution or use non-execution health and
  telemetry interfaces.

Proposed fix:

- Remove `diagnostic_sync_run()` and `diagnostic_async_run()` from the QPM
  service API and shared QPM implementation.
- Remove `diagnostic_read_cq()` and `diagnostic_peek_cq()`.
- Remove `QFW_QPM_DIAGNOSTIC_BYPASS_ENABLED` and diagnostic bypass request
  flags.
- Remove diagnostic bypass audit records, telemetry fields, tests, and dead
  controller state.
- Update requirements, detailed design, implementation plan, and test plan to
  remove the diagnostic execution bypass while preserving the intent of
  `SCHED-010`: no unprotected path may bypass admission or scheduling.

Resolution:

- Removed the diagnostic execution and provider completion-queue methods.
- Removed diagnostic request state, controller state, telemetry, audit records,
  environment configuration, and tests.
- All public circuit execution now requires a reservation and follows admission
  authorization and scheduler selection.

## Public Pending-Capacity Retry API

Status: open

The QPM admission control API exposes `retry_pending_capacity()` as a remote
operation. This allows a caller-facing admission client to force QPM to retry
pending execution work. Retrying managed qtasks is controller behavior, not a
reservation lifecycle operation, and ordinary admission clients should not
control global queue processing.

Expected behavior:

- QPM retries pending work internally when capacity is released or becomes
  available.
- Task completion, cancellation, reservation lifecycle changes, policy changes,
  and periodic reconciliation trigger retry processing where appropriate.
- Admission clients manage reservations but cannot force scheduler or pending
  queue processing through a remote API.

Proposed fix:

- Remove `retry_pending_capacity()` from the QPM admission control service API.
- Remove the public `UTIL_QPM.retry_pending_capacity()` RPC wrapper.
- Retain the controller's internal retry operation for event-driven and
  reconciliation paths.
- Remove or rewrite tests that treat pending-capacity retry as a caller-facing
  operation.
- Update requirements, detailed design, implementation plan, and test plan so
  retry behavior is specified as an internal QPM lifecycle responsibility.

## Duplicate Admission Policy Configuration APIs

Status: open

The QPM admission policy configuration surface exposes both
`configure_admission_policy()` and `set_admission_policy()`. Both operations
ultimately call the same controller `set_admission_policy()` implementation,
but they accept different public payload shapes. The configuration operation
also applies estimator configuration through a second controller call, so the
policy and estimator update is not atomic.

Current callers use both forms. The fake-IQM stress test passes a policy
dictionary to `set_admission_policy()`, while other tests use named policy and
estimator arguments through `configure_admission_policy()`. This creates two
schemas for the same service operation and allows callers to observe a
partially updated policy configuration.

Expected behavior:

- QPM exposes only `get_admission_policy()` and `set_admission_policy()` for
  admission policy configuration.
- `set_admission_policy()` accepts one structured configuration containing the
  device, policy, estimator, policy-specific capacity, options, and expected
  configuration version where applicable.
- Policy, estimator, baseline, and capacity configuration is validated and
  applied atomically.
- Callers use one stable payload schema.
- `get_admission_policy()` returns the complete normalized configuration and
  configuration version.

Proposed fix:

- Keep `get_admission_policy()` and `set_admission_policy()` as the canonical
  remote APIs.
- Remove `configure_admission_policy()`, `get_capacity_model()`,
  `set_capacity_model()`, `get_estimator_policy()`, and
  `set_estimator_policy()` from the public service API.
- Define policy-specific capacity schemas. `unlimited` requires no capacity;
  `credit` accepts credit budget and credit-policy options; `rate` accepts
  throughput, accounting-window, and rate-policy options.
- Define the baseline estimator with a complete baseline circuit shape:
  `qubit_count`, `depth`, `one_q_gate_count`, `two_q_gate_count`, `shots`, and
  `measurement_count`. Do not expose unsupported `baseline_depth` and
  `baseline_shots` estimator options.
- Keep physical device timing and limits in the device profile. Translate the
  baseline portion of admission configuration into the qhw-admission baseline
  associated with that profile.
- Validate the policy, estimator, baseline, and capacity configuration before
  changing live state. Apply all components as one transaction or leave the
  previous configuration unchanged.
- Use qhw-admission's estimator output as the common bridge: admission policies
  consume `baseline_units` and `total_ns`; QPM passes `total_ns` as
  `estimated_runtime_ns` and `baseline_units` as `estimated_cost` to
  qhw-scheduler. qhw-scheduler must not interpret the baseline circuit itself.
- Migrate examples, the fake-IQM stress fixture, site-driver configuration, and
  mock tests to the single `set_admission_policy()` payload.
- Add tests for credit and rate schema validation, baseline-unit estimation,
  derived and explicit capacity, scheduler estimate propagation, and atomic
  rollback after invalid configuration.
- Retain private controller setters only as implementation helpers where they
  remain useful.
- Update requirements, detailed design, implementation plan, and test plan to
  define the single configuration schema, policy-specific capacity, baseline
  estimation, scheduler handoff, and atomic update behavior.

## Enforce Device Provider Queue Depth In QPMController

Status: open

The qhw-admission device profile already carries
`max_provider_queue_depth`, and QFw preserves it when registering a device.
QPMController does not currently read that field when deciding whether it can
select and submit another scheduler task. Only the operator-configured
`dispatch_depth` limits `provider_inflight`, so QPM can exceed the provider
queue capability recorded in the device profile.

This is a QFw implementation gap. qhw-admission already stores the required
device field, and qhw-scheduler should continue to order tasks without knowing
about the downstream provider queue. Neither library requires an API change.

Expected behavior:

- `max_provider_queue_depth` remains a device capability configured through
  the QPM device profile.
- `max_inflight` remains an operator-configured QPM dispatch limit.
- QPMController calculates the effective dispatch limit as the smaller nonzero
  value of `max_inflight` and `max_provider_queue_depth`.
- A zero value means that the corresponding limit is unspecified.
- QPMController enforces the effective limit before selecting another task from
  qhw-scheduler.
- Lowering a limit does not cancel submitted work; it prevents new dispatch
  until provider occupancy falls below the effective limit.
- Completion, cancellation, timeout, and provider-submission failure release a
  provider slot and trigger dispatch of the next eligible scheduler task.

Proposed fix:

- Read `max_provider_queue_depth` from QPMController's normalized device
  profile.
- Replace the direct `dispatch_depth` check in
  `_can_select_scheduler_task_locked()` with a calculated effective limit.
- Treat `provider_inflight` as the conservative count of submitted but
  nonterminal provider work.
- Make device-profile updates immediately refresh the effective limit used by
  the controller.
- Report the device limit, configured operator limit, effective limit, and
  current provider occupancy through scheduler status and queue telemetry.
- Add tests for device-only limits, operator-only limits, combined limits,
  unspecified limits, dynamic limit changes, and slot release on every
  terminal or failed-submission path.
- Update requirements, detailed design, implementation plan, and test plan to
  assign provider queue-depth enforcement to QPMController.

## Consolidate QPM Scheduler Control APIs

Status: open

The QPM scheduler control surface contains duplicate policy and lifecycle
operations. It exposes both `configure_scheduler_policy()` and
`set_scheduler_policy()`, as well as target-specific pause, resume, and drain
operations plus shorter aliases. `set_dispatch_depth()` also exposes a scalar
name that does not support a structured dispatch-limit contract.

Target `api_qpm_scheduler_control` surface:

- `configure_scheduler_policy(token, device_id, configuration)` configures the
  scheduler policy and policy options.
- `get_scheduler_policy(token, device_id)` returns the normalized active policy,
  options, and version.
- `get_scheduler_status(token, device_id)` returns running, paused, or draining
  state together with configured, device, and effective dispatch limits.
- `pause_execution_target(token, device_id, reason)` stops new dispatch while
  retaining queued work.
- `resume_execution_target(token, device_id)` resumes selection and dispatch.
- `drain_execution_target(token, device_id, mode, timeout_s)` stops new
  dispatch and handles selected, submitted, and running work according to the
  requested drain policy.
- `configure_dispatch_limits(token, device_id, limits)` atomically configures
  operator dispatch limits. The initial supported field is `max_inflight`;
  `max_provider_queue_depth` remains part of the device profile.
- `get_scheduler_queue_state(token, device_id, include_restricted)` returns
  scheduler queue and ordering state permitted to the caller.

Proposed fix:

- Remove `set_scheduler_policy()` and migrate callers to
  `configure_scheduler_policy()`.
- Remove the `pause()`, `resume()`, and `drain()` aliases and migrate callers to
  the execution-target operations.
- Replace `set_dispatch_depth()` with `configure_dispatch_limits()` and a
  structured limits payload.
- Keep policy configuration, target lifecycle control, dispatch-limit control,
  and queue inspection as distinct method contracts.
- Update service API definitions, UTIL_QPM wrappers, controller entry points,
  service binding tests, fake-IQM stress tests, documentation, and system-test
  expectations to use only the consolidated surface.

## Consolidate QPM Telemetry And Task Information APIs

Status: open

The QPM telemetry surface contains task-specific APIs inherited from the
original monolithic QPM API, including `get_last_job_timing()` and
`get_last_job_metadata()`. The word `last` is ambiguous and unsafe when several
clients or reservations execute concurrently. Task timing and task metadata
belong to the caller-facing managed execution lifecycle, not the aggregate
telemetry category.

The telemetry surface also includes `test()`, `shutdown()`, and
`reconcile_runtime_state()`, which are not telemetry operations. `test()` is an
unstructured legacy probe, while shutdown and reconciliation mutate service
state. These operations belong to the privileged `api_qpm_control` category and
must not be available to ordinary application clients.
`max_provider_queue_depth` is a device-profile capability enforced by
QPMController, not a separate telemetry API.

Target task-information APIs in `api_qpm_execution`:

- `get_task_timing(token, reservation_id, task_id)` returns timing information
  for the identified QPM task after validating caller and reservation scope.
- `get_task_metadata(token, reservation_id, task_id)` returns managed lifecycle,
  scheduler, provider, and result metadata permitted to the caller.

Expected behavior:

- Task identity is explicit; no API infers a task from global or per-service
  "last job" state.
- Task information uses the QPM `task_id` as its required lookup key.
- Reservation identity and caller credentials authorize access but do not
  replace the task lookup key.
- `max_provider_queue_depth` remains in the device profile and is reported as
  scheduler/device-limit state where needed for operator inspection.
- `test()`, `is_ready()`, and structured service status are exposed through the
  privileged `api_qpm_control` binding.
- Service shutdown remains owned by QFw teardown or site/operator tooling and
  is exposed only through the privileged `api_qpm_control` binding.
- Runtime reconciliation is an authorized QPM control operation, not a
  telemetry query.

Target `api_qpm_telemetry` surface:

- `get_backend_info()` returns provider and backend software information.
- `get_device_info()` returns physical and logical device properties.
- `get_dynamic_backend_info()` returns dynamic provider state only where it is
  distinct from calibration data.
- `get_calibration_snapshot()` returns calibration data identified by
  calibration set or timestamp.
- `get_coupling_graph()` returns device topology.
- `get_telemetry_access_model()` describes available telemetry access classes.
- `get_capacity_snapshot()` returns admission capacity and held-capacity state.
- `get_queue_metrics()` returns aggregate pending, scheduler, and provider queue
  metrics.
- `get_service_lifecycle_telemetry()` returns read-only service lifecycle and
  health information.

Proposed fix:

- Replace `get_last_job_timing()` with task-ID-based `get_task_timing()` under
  `api_qpm_execution`.
- Replace `get_last_job_metadata()` and the telemetry form of
  `get_task_metadata()` with one task-ID-based `get_task_metadata()` under
  `api_qpm_execution`.
- Move `test()` and `is_ready()` to `api_qpm_control`; make `test()` a
  structured, provider-independent liveness check that submits no work.
- Move `shutdown()` to `api_qpm_control`. Implement an authorized shutdown
  protocol that quiesces new work, drains or cancels according to mode,
  reconciles accounting, stops workers and the provider, deregisters the
  service, acknowledges the request, and then exits.
- Do not introduce a telemetry API for `max_provider_queue_depth`; preserve it
  in device configuration and expose configured and effective enforcement
  state through scheduler status where authorized.
- Move `reconcile_runtime_state()` to `api_qpm_control`. Preserve its current
  repair behavior for invalid pending entries, inactive-reservation holds,
  stale runtime mappings, scheduler inconsistencies, and provider-handle
  faults; require operator authorization and an audit reason.
- Update QPM service API definitions, provider implementations, Qiskit adapter
  calls, examples, readiness checks, tests, requirements, detailed design,
  implementation plan, and test plan for the consolidated contracts.

## Add Privileged QPM Control API Category

Status: open

QPM process-lifecycle operations are currently mixed into execution and
telemetry. `is_ready()` is declared under execution, while `test()`,
`shutdown()`, and `reconcile_runtime_state()` are declared under telemetry.
This gives application-oriented bindings access to operator behavior and leaves
shutdown without a complete service termination protocol.

Target `api_qpm_control` surface:

- `test(token)` returns structured QPM RPC and process liveness without
  submitting provider work.
- `is_ready(token)` reports initialized and request-acceptance readiness.
- `get_service_status(token)` returns structured lifecycle state, readiness,
  directory registration, provider readiness, active reservations, active
  tasks, and shutdown state.
- `reconcile_runtime_state(token, reason)` performs the existing runtime repair
  operation and records the authorized reason and repair summary.
- `shutdown(token, mode="graceful", timeout_s=None, reason=None)` performs a
  service-owner shutdown. Supported initial modes are `graceful`, which drains
  active work, and `cancel`, which cancels active work before cleanup.

Expected shutdown sequence:

1. Validate service-owner or site-operator authorization.
2. Enter `quiescing` and reject new reservations and execution submissions.
3. Drain or cancel pending, scheduler, and provider work according to mode.
4. Finalize admission holds, usage accounting, and reservation-scoped state.
5. Stop completion, purge, retry, and other QPM background workers.
6. Release provider credentials and shut down the QRC/provider runtime.
7. Deregister the QPM service from DEFw-dirsvc.
8. Return an acknowledgement before scheduling DEFw process exit.

Proposed fix:

- Add `service-apis/api_qpm_control` as a concrete DEFw API binding advertised
  by every QPM service.
- Move the remote definitions and wrappers for `test()`, `is_ready()`,
  `reconcile_runtime_state()`, and `shutdown()` into that category.
- Add `get_service_status()` and explicit QPM lifecycle states such as
  `initializing`, `running`, `quiescing`, `draining`, `stopping`, and `failed`.
- Keep `release_service()` and `shutdown_provider()` as internal lifecycle
  helpers rather than remote APIs.
- Replace provider-specific `test()` strings with one structured liveness
  response.
- Ensure `shutdown()` sends its RPC response before terminating the DEFw
  process and is idempotent when shutdown is already in progress.
- Audit every reconciliation and shutdown request without logging credentials.
- Migrate setup readiness probes, resolver checks, examples, teardown tooling,
  and tests to the `api_qpm_control` binding.
- Add tests for authorization, readiness states, reconciliation side effects,
  graceful and cancelling shutdown, repeated shutdown, timeout handling,
  directory deregistration, provider cleanup, and long-running QPM ownership.
- Update requirements, detailed design, implementation plan, and test plan to
  define the new category and its lifecycle state machine.

## Repository-Wide QPM API Migration Scrub

Status: open

After the category contracts and directory-owned service APIs are implemented,
perform a detailed QFw and DEFw scrub so no runtime path, installed artifact,
test, or document relies on the old aggregate QPM API. This is a breaking
internal API migration; compatibility wrappers and aliases are out of scope.

QFw scrub scope:

- Move every remote API class and method declaration into its owning
  `service-apis/api_qpm_*` directory.
- Remove the aggregate `api_qpm.QPM` class and central category class
  declarations.
- Remove old method aliases and methods selected for deletion, including
  diagnostic bypass, public pending retry, duplicate admission and scheduler
  setters, short scheduler lifecycle aliases, and last-job telemetry methods.
- Update QPM binding constants, registration metadata, startup records,
  resolver defaults, selected-binding construction, and service discovery to
  use the new module and class names.
- Update server-side UTIL_QPM wrappers and QPMController entry points to match
  the exact category signatures in this document.
- Update all provider QPM implementations, QRC integrations, Qiskit adapters,
  launchers, setup and teardown commands, site drivers, examples, and manual
  tools to request the correct category binding.
- Update CMake install rules, activation paths, package imports, installed-tree
  checks, and service API discovery so every category package is installed and
  importable independently.
- Remove stale configuration keys, environment variables, comments, generated
  expectations, and tests that describe deleted APIs.

DEFw scrub scope:

- Verify DEFw loads each category package as an independent service API module
  and constructs the requested `BaseRemote` class from binding metadata.
- Verify one registered QPM service can advertise several client module/class
  bindings that all route to the same server module/class and endpoint.
- Remove any QPM-specific assumptions, fixtures, or tests that require
  `api_qpm.QPM` or a default aggregate binding. DEFw remains category-agnostic;
  it stores and routes opaque module/class binding metadata.
- Update DEFw service-loading, remote-class resolution, serialization, and
  multi-binding tests only where the new package layout exposes a real generic
  defect. Do not add QPM category policy to DEFw.

Verification requirements:

- Search QFw and DEFw for every removed class, module, method, alias,
  environment variable, and binding name; justify any remaining occurrence.
- Add import and signature tests for each directory-owned API package.
- Add registration and resolver tests proving that each category selects the
  correct client class while reaching the same logical QPM service.
- Run focused controller tests for admission, scheduling, telemetry, service
  control, reconciliation, dispatch limits, and shutdown.
- Run DEFw smoke and framework tests, QFw installed-tree smoke tests, and
  representative local and heterogeneous examples.
- Confirm normal application cleanup cannot resolve or call privileged QPM
  control operations and that operator tooling can explicitly resolve the
  `api_qpm_control` binding.
- Update requirements, detailed design, implementation plan, test plan, README,
  and service API documentation to exactly match the final code surface.
