# QPMd Supported APIs

The APIs below are the callable QPMd service methods exposed by the
`service-apis/api_qpm*` packages. The list is organized by service category
and omits parameters intentionally. `api_qpm_common` provides shared constants,
binding helpers, and base types rather than a remote API surface.

## Admission Policy Configuration

Admission policy configuration is important because it defines the device
profile and policy inputs that QPMd uses before accepting work. These APIs are
the operator-facing control plane for capacity, timing, estimator, and policy
configuration.

| API | Description |
| --- | --- |
| `configure_device_profile` | Store or update the admission device profile used for capacity, timing, and provider-limit decisions. |
| `get_device_profile` | Return the currently configured admission device profile and its version. |
| `get_admission_policy` | Return the active normalized admission policy configuration and version for a device. |
| `set_admission_policy` | Validate and atomically activate a complete admission policy configuration for a device. |

## Admission Control

Admission control is important because it turns policy into reservation
decisions. These APIs let workflow managers, resource managers, and launchers
ask whether work can run, create a reservation, and manage that reservation's
lifecycle.

| API | Description |
| --- | --- |
| `evaluate` | Evaluate whether a requested workload would be accepted, delayed, or rejected without creating a reservation. |
| `reserve` | Create an accepted reservation and return its lifecycle state and expiration information. |
| `renew` | Extend an existing reservation lifetime when policy allows it. |
| `release` | Close a reservation gracefully, stop new reservation-scoped work, finalize accounting, and mark it released. |
| `cancel` | Cancel a reservation, cancel or fail reservation-scoped work according to policy, finalize accounting, and mark it cancelled. |
| `get_reservation` | Return reservation state, owner metadata, expiration, allowance, and usage summary. |
| `list_reservations` | Return reservation summaries matching device, owner, job, state, or time filters. |

## Scheduler Control

Scheduler control is important because admission only says work may run; the
scheduler decides when work is dispatched. These APIs let operators configure
scheduling policy, pause or drain execution targets, and inspect queue state.

| API | Description |
| --- | --- |
| `configure_scheduler_policy` | Validate and activate the scheduler policy for a QPM-managed execution target. |
| `get_scheduler_status` | Return scheduler state, availability, policy, task counts, and effective dispatch limits. |
| `get_scheduler_policy` | Return the active scheduler policy, options, and version. |
| `pause_execution_target` | Stop dispatching newly selected tasks while preserving scheduler queue state. |
| `resume_execution_target` | Re-enable scheduler dispatch for a paused execution target. |
| `drain_execution_target` | Stop new dispatch and let selected or running work finish according to the requested drain policy. |
| `configure_dispatch_limits` | Atomically update operator dispatch limits such as maximum in-flight work. |
| `get_scheduler_queue_state` | Return scheduler queue state for the requested access scope. |

## Execution

Execution is important because it is the resource-affecting path applications
use to submit quantum work and observe its lifecycle. These APIs connect
reserved work to QPMd admission, scheduling, completion queues, events,
status, cancellation, timing, and metadata.

| API | Description |
| --- | --- |
| `delete_circuit` | Remove client-visible circuit state when lifecycle and retention policy allow it. |
| `sync_run` | Submit work through admission and scheduling, then block until a terminal result or structured timeout/delay/cancellation/failure status. |
| `async_run` | Submit work through admission and scheduling and return the circuit, QPM task, scheduler task, and lifecycle handles when available. |
| `read_cq` | Return and consume one completion record from the reservation-scoped completion queue. |
| `peek_cq` | Return one completion record from the reservation-scoped completion queue without consuming it. |
| `register_event_notification` | Register event delivery for task lifecycle events, optionally scoped by reservation and filters. |
| `cancel_task` | Cancel pending, queued, selected, or provider-submitted work and update admission accounting. |
| `task_status` | Return the managed lifecycle state for a circuit or QPM task. |
| `get_task_timing` | Return provider and QPM timing for a reservation-scoped task. |
| `get_task_metadata` | Return permitted lifecycle, scheduler, provider, and result metadata for a reservation-scoped task. |

## Telemetry And Discovery

Telemetry and discovery are important because QPMd must expose what the target
can do, how QRMI and QDMI coverage maps onto the target, and what aggregate
capacity, queue, allocation, and lifecycle state are visible to callers.

| API | Description |
| --- | --- |
| `get_backend_info` | Return backend metadata, with optional QRMI or QDMI library routing for shim QPMs. |
| `get_device_info` | Return device properties, with optional QRMI or QDMI library routing for shim QPMs. |
| `get_dynamic_backend_info` | Return dynamic backend metadata for a calibration context. |
| `get_calibration_snapshot` | Return calibration data for a device or calibration context. |
| `get_coupling_graph` | Return device topology or coupling-graph data. |
| `capability_map` | Return the library coverage map showing which wired QRMI or QDMI resources support each contract call. |
| `get_telemetry_access_model` | Return the telemetry access classes and the class assigned to each telemetry method. |
| `get_capacity_snapshot` | Return admission capacity, held capacity, active reservation, queue-depth, and confidence information. |
| `get_queue_metrics` | Return aggregate queue, scheduler, active-task, dispatch-limit, and wait-estimate metrics. |
| `get_service_lifecycle_telemetry` | Return service lifecycle events, audit records, and reconciliation faults. |
| `list_scheduler_allocations` | Return scheduler allocation summaries derived from reservation and scheduler state. |

The Qiskit client binds this API next to the execution API, as
`QFwBackend.telemetry`, and builds `QFwBackend.target` from `get_backend_info`,
`get_device_info`, `get_coupling_graph` and `get_calibration_snapshot` (see the
Device Target section of `usage.md`). Both shim libraries serve
`get_backend_info`: QRMI from `target()`, with the provider's static
architecture and, for an IBM resource, the raw backend configuration under
`static_architecture` and the raw backend properties under
`backend_properties`; QDMI from the device topology, with the same embedded
qhw device record and an empty static architecture, so a QDMI-only resource
describes itself too. `get_dynamic_backend_info` stays with QRMI.

## Privileged Control

Privileged control is important because service owners and site operators need
a distinct surface for health checks, readiness, reconciliation, summaries, and
shutdown without mixing those controls into ordinary application execution
paths.

| API | Description |
| --- | --- |
| `test` | Report structured RPC, initialization, and process liveness without submitting provider work. |
| `is_ready` | Report whether the service is initialized, provider-ready, and accepting requests. |
| `get_service_status` | Return service lifecycle state, readiness, active reservation and task counts, provider state, and shutdown state. |
| `get_service_summary` | Return a compact operational summary of QPMd readiness, activity, maintenance state, assigned hosts, and timestamp. |
| `reconcile_runtime_state` | Repair runtime mappings and accounting, then record the audit reason and reconciliation summary. |
| `shutdown` | Quiesce or gracefully drain the service before asynchronous service termination. |
