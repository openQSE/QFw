# Design Review Updates

Run ID: 2026-07-27T220849+0000-2452c04b
Design artifact: docs/detailed-design.md
Design review summary: docs/design-review.md

This log is maintained by the ElectroBoy orchestrator. It records
detailed-design changes made by the design author in response to
design-review findings.

## Update Entries

### Update After Review Pass 1

Recorded: 2026-07-30T17:51:05+00:00
Review event: agent-00043
Design author event: agent-00045
Review issue file: design-review.jsonl

#### Blocking Findings

- DR-P1-001: major open - Reservation lifecycle states do not match current qhw-admission states.
- DR-P1-002: major open - Estimated capacity hold semantics are not mapped to concrete qhw-admission calls.
- DR-P1-003: major open - Managed task lifecycle mapping to qhw-scheduler states is undefined.
- DR-P1-004: major open - Long-running QPM startup omits the current directory-service initialization gate.
- DR-P1-005: major open - Qiskit adapter migration for reservation-scoped execution is undefined.
- DR-P1-006: major open - QPM controller threading mode for admission and scheduler contexts is unspecified.

#### Changed Files

- docs/detailed-design.md

#### Design Author Summary

Updated [docs/detailed-design.md](/home/a2e/ORNL/Quantum/openQSE/QFw/docs/detailed-design.md) for design review pass 1.

Changes made:
- Aligned reservation lifecycle with qhw-admission states: `PENDING`, `ACTIVE`, `RELEASED`, `EXPIRED`, `CANCELLED`; moved renew and over-limit to operation/compliance semantics.
- Mapped estimated capacity holds to concrete qhw-admission calls: `authorize_usage()`, `consume()`, `return_usage()`, and `record_actual()`.
- Added managed task lifecycle mapping to qhw-scheduler states and calls, including QPM overlays for `SUBMITTED` and `TIMED_OUT`.
- Defined long-running QPM startup behavior around `DEFW_DISABLE_DIRSVC`, `qpm_wait_dirsvc()`, resolver health checks, and a `startup-readiness-gate`.
- Defined Qiskit adapter migration through a QPM resolver and reservation-aware `QFwBackend.run()` / `QFwJob` request flow.
- Specified QPM controller threading defaults and constraints for qhw-admission and qhw-scheduler contexts.

Verification:
- `git -C QFw diff --check -- docs/detailed-design.md` passed.
- Appended required progress updates to the ElectroBoy progress file.

```yaml
changed_files:
  - QFw/docs/detailed-design.md
  - QFw/.electroboy/shared/runs/2026-07-27T220849+0000-2452c04b/progress/design-review-update-progress.md
```

#### Detailed Design Diff

```diff
--- a/docs/detailed-design.md
+++ b/docs/detailed-design.md
@@ -153,11 +153,24 @@
 | Manager aggregate state | Aggregate queue depth, capacity summaries, reservation summaries, and workload-level wait estimates. | Workflow managers, load managers, trusted automation. |
 | Operator telemetry | Policy state, scheduler internals, cross-user views, audit records, and detailed operational health. | Site operators, administrators, trusted monitoring services. |
 
-Lifecycle semantics should be defined before API names or bindings. The
-reservation lifecycle covers accepted, delayed, rejected, active, renewed,
-released, cancelled, expired, and over-limit states. The managed qtask
-lifecycle covers pending for capacity, queued, selected, submitted to the
-provider, running, completed, failed, cancelled, and timed out.
+Lifecycle semantics should be defined before API names or bindings.
+qhw-admission uses decision kinds for request outcomes and reservation states
+for committed reservations. Request outcomes are accepted, delayed, and
+rejected. The concrete reservation states are
+`QHW_ADM_RESERVATION_PENDING`, `QHW_ADM_RESERVATION_ACTIVE`,
+`QHW_ADM_RESERVATION_RELEASED`, `QHW_ADM_RESERVATION_EXPIRED`, and
+`QHW_ADM_RESERVATION_CANCELLED`. Renew is an operation that extends an active
+reservation. Over-limit is reported through reason, usage, and compliance data
+rather than a reservation state.
+
+The managed qtask lifecycle covers pending for capacity, queued, selected,
+submitted to the provider, running, completed, failed, cancelled, and timed
+out. qhw-scheduler provides the concrete scheduler states
+`QHW_SCHED_TASK_QUEUED`, `QHW_SCHED_TASK_ASSIGNED`,
+`QHW_SCHED_TASK_RUNNING`, `QHW_SCHED_TASK_COMPLETED`,
+`QHW_SCHED_TASK_FAILED`, `QHW_SCHED_TASK_CANCELLED`, and
+`QHW_SCHED_TASK_WAITING`. QPM adds pending-capacity, submitted-provider, and
+timed-out overlays where those concepts live outside qhw-scheduler.
 
 </details>
 
@@ -327,6 +340,29 @@
 - dispatch of selected qtasks to the QRC provider path
 - completion handling back into scheduler and admission state
 
+Each controller instance creates both library contexts with explicit threading
+attributes. The default QPM service configuration uses
+`QHW_ADM_THREAD_SAFE` for qhw-admission and `QHW_SCHED_THREAD_SAFE` for
+qhw-scheduler because DEFw RPC handlers, dispatcher threads, timeout handling,
+and QRC completion callbacks can touch the same target state. The Python
+qhw-admission wrapper defaults to caller-serialized `THREAD_USER`, so QPM must
+override that default during context construction.
+
+A deployment may select `QHW_ADM_THREAD_USER` and `QHW_SCHED_THREAD_USER` only
+when the QPM controller runs all calls for that execution target through one
+serialized event loop or one controller lock. The selected mode must be recorded
+in controller telemetry. Even in thread-safe library mode, QPM uses its own
+target controller lock around compound transitions that update QPM mappings,
+qhw-admission usage, and qhw-scheduler state together. The library locks
+protect each context internally; they do not make a multi-library transition
+atomic.
+
+Provider calls are outside the controller lock. QPM records the dispatch state,
+releases the lock, calls QRC or the provider adapter, and then re-enters the
+controller on completion, failure, timeout, or cancellation. This avoids
+blocking admission and status calls behind long provider operations while
+preserving ordered lifecycle updates.
+
 The controller does not become the source of truth for library configuration.
 `qhw-admission` maintains device profiles, admission policy, estimator
 configuration, reservation state, and usage records. `qhw-scheduler` maintains
@@ -354,11 +390,14 @@
     Client->>TaskAPI: async_run(info, reservation_id, token)
     TaskAPI->>QPM: DEFw RPC call
     QPM->>UTIL: submit reservation-scoped qtask
-    UTIL->>ADM: verify reservation and caller binding
-    ADM-->>UTIL: active / unauthorized / invalid / expired
-    UTIL->>ADM: hold estimated qtask capacity
-    ADM-->>UTIL: held / delayed / rejected
-    UTIL->>SCHED: insert qtask
+    UTIL->>ADM: expire(now), get_reservation(reservation_id)
+    ADM-->>UTIL: reservation record or lifecycle error
+    UTIL->>UTIL: compare caller binding and request scope
+    UTIL->>ADM: authorize_usage(reservation_id, estimated usage)
+    ADM-->>UTIL: accepted / delayed / rejected
+    UTIL->>ADM: consume(reservation_id, estimated usage)
+    ADM-->>UTIL: accepted hold / delayed / rejected
+    UTIL->>SCHED: submit_task(qtask descriptor)
     SCHED-->>UTIL: scheduler task id
     UTIL-->>Client: qtask id and queued status
 
@@ -368,8 +407,8 @@
     QRC->>Provider: provider submission
     Provider-->>QRC: result or terminal provider state
     QRC-->>UTIL: completion callback or completion record
-    UTIL->>SCHED: update task lifecycle
-    UTIL->>ADM: release hold and record actual usage
+    UTIL->>SCHED: task_completed(), task_failed(), or task_cancelled()
+    UTIL->>ADM: return_usage() and/or record_actual()
     UTIL-->>TaskAPI: completion state
     TaskAPI-->>Client: event, read_cq(), status, or result response
 ```
@@ -382,6 +421,41 @@
 still hold a bounded number of selected qtasks when a backend benefits from
 prefetching, but provider queue depth is a scheduler-control setting rather
 than an accidental side effect of asynchronous QRC dispatch.
+
+The qhw-admission call sequence uses `qhw_adm_usage_t.task_id` as the
+idempotency key for estimated usage. QPM fills that field with the QPM qtask ID
+and stores the matching QFw circuit ID and scheduler task ID in its runtime
+mapping. The estimated capacity hold is the accepted `consume()` decision. A
+qtask that cannot obtain an accepted `consume()` decision remains out of
+qhw-scheduler and either fails, delays, or enters the QPM pending-capacity
+queue according to site policy. If a consumed qtask is cancelled before
+provider execution, fails before execution, or uses less than the charged
+estimate, QPM calls `return_usage()` once for the unused amount. After provider
+completion, QPM calls `record_actual()` with `qhw_adm_actual_usage_t` before
+publishing the terminal result. Repeating a usage operation with the same
+nonzero task ID must use identical usage data so qhw-admission can apply its
+idempotency rules.
+
+Managed task status is a QPM-facing view over QPM pending state,
+qhw-scheduler task state, dispatcher state, and provider state:
+
+| QPM status | Concrete scheduler state or owner | Required transition |
+| --- | --- | --- |
+| `PENDING_CAPACITY` | QPM pending queue; no scheduler task exists. | Entered after `authorize_usage()` or `consume()` returns delayed and site policy queues the qtask. |
+| `QUEUED` | `QHW_SCHED_TASK_QUEUED`. | Entered after accepted `consume()` and successful `qhw_sched_submit_task()`. |
+| `WAITING` | `QHW_SCHED_TASK_WAITING`. | Used for a sliced parent while child qtasks are queued or running. |
+| `SELECTED` | `QHW_SCHED_TASK_ASSIGNED`. | Entered after `qhw_sched_select_next()` returns the assignment. |
+| `SUBMITTED` | QPM dispatcher overlay on `ASSIGNED`. | Entered after QPM hands selected work to QRC or the provider adapter. |
+| `RUNNING` | `QHW_SCHED_TASK_RUNNING`. | Entered after QPM calls `qhw_sched_task_started()` when provider execution starts or provider acceptance is the first observable running point. |
+| `COMPLETED` | `QHW_SCHED_TASK_COMPLETED`. | Entered through `qhw_sched_task_completed()` before final result publication. |
+| `FAILED` | `QHW_SCHED_TASK_FAILED`. | Entered through `qhw_sched_task_failed()` for scheduler, dispatcher, provider, or reconciliation failure. |
+| `CANCELLED` | `QHW_SCHED_TASK_CANCELLED` or QPM pending cancellation. | Entered through `qhw_sched_task_cancelled()` when a scheduler task exists, or by removing a pending-capacity entry before scheduler insertion. |
+| `TIMED_OUT` | QPM response overlay. | Returned when a synchronous waiter expires while the underlying qtask remains in its current non-terminal state. |
+
+`SUBMITTED` and `TIMED_OUT` are not qhw-scheduler states. They are stable QPM
+API states derived from dispatcher and waiter context. A provider that does not
+distinguish accepted and running work may move directly from `ASSIGNED` to
+`RUNNING` when the provider accepts the submission.
 
 ### DEFw Directory And Identity Model
 
@@ -614,7 +688,7 @@
 | `prepare_circuit(info)` | Create and provider-decorate a QFw circuit record before admission and scheduling. Existing `create_circuit()` overrides can migrate here. |
 | `prepare_provider_submission(circuit)` | Apply provider-specific launch metadata after capacity has been held and before scheduler insertion or dispatch. QB can use this for vQPU configuration. |
 | `submit_scheduled_circuit(circuit, mode)` | Submit only scheduler-selected work to QRC for synchronous or asynchronous execution. |
-| `complete_scheduled_circuit(cid, result)` | Update scheduler lifecycle state, release or consume admission holds, record actual usage, and publish result state. |
+| `complete_scheduled_circuit(cid, result)` | Update scheduler lifecycle state, return unused consumed capacity, record actual usage, and publish result state. |
 | `cancel_scheduled_circuit(cid, reservation_id, reason)` | Propagate cancellation through pending state, scheduler state, provider handles, result state, and admission accounting. |
 
 ### QFw API Categories
@@ -850,6 +924,15 @@
 turning the long-running service into a separate non-DEFw protocol while
 removing the requirement that a DEFw-dirsvc always be present.
 
+The current QPM modules mark themselves ready only after `defw.dirsvc` exists.
+Long-running mode must replace that readiness gate. A long-running QPM is ready
+when its DEFw listener is accepting RPC calls, its QRC provider path is
+initialized, its qhw-admission and qhw-scheduler contexts are constructed, and
+the target device profile and policies have been loaded. Optional
+DEFw-dirsvc registration may happen later or not at all. Failure to register
+with a dirsvc must not keep a configured long-running service in
+`DEFwNotReady` when the service is otherwise callable by endpoint.
+
 </details>
 
 <details>
@@ -914,6 +997,22 @@
 | `register-with-dirsvc` | Boolean controlling whether the service registers with DEFw-dirsvc. |
 | `listen-endpoint` | Stable endpoint or port used by long-running clients. |
 | `dirsvc-endpoint` | Optional DEFw-dirsvc endpoint for QFw-managed registration. |
+| `startup-readiness-gate` | `dirsvc-ready` for registered mode or `listener-and-controller-ready` for long-running endpoint mode. |
+
+The option must map to the existing DEFw startup behavior. `defwp-wrapper`
+defaults `DEFW_DISABLE_DIRSVC` to `yes`, and the C listener attempts a parent
+directory-service connection only when directory-service use is enabled and a
+parent name is configured. QFw-managed service launch sets
+`DEFW_DISABLE_DIRSVC=no` and provides parent host, port, and name. A
+long-running unregistered QPM should set `DEFW_DISABLE_DIRSVC=yes`, leave
+registration disabled, and use the listener/controller readiness gate above.
+
+Provider QPM modules that currently wait in `qpm_wait_dirsvc()` need a
+configuration-aware readiness path. In registered mode they may keep the
+existing dirsvc wait. In endpoint mode they should call the common QPM
+completion routine after listener and controller initialization, then expose
+health and metadata over DEFw RPC so the configured-endpoint resolver can
+validate the service.
 
 </details>
 
@@ -954,10 +1053,11 @@
 3. Connect to the DEFw-wrapped service.
 4. Reload active service-agent state so DEFw learns the service's current
    remote UUID and block UUID from the connection handshake.
-5. Query the connected service for QPM service metadata.
-6. Verify that the service metadata matches the requested service name,
+5. Call the service readiness or health method.
+6. Query the connected service for QPM service metadata.
+7. Verify that the service metadata matches the requested service name,
    provider, device ID, type, and capabilities.
-7. Return the same QPM client binding shape used by the DEFw-dirsvc discovery
+8. Return the same QPM client binding shape used by the DEFw-dirsvc discovery
    path.
 
 After resolution, reservation and release behavior should be identical for
@@ -1046,6 +1146,14 @@
 cancelled, and matches the requested job, session, scope, target device, and
 operation type.
 
+The concrete check is a controller sequence. QPM first calls
+`qhw_adm_expire()` or the Python `expire()` wrapper with the controller's
+current time policy, then calls `get_reservation(reservation_id)`. The
+reservation is usable only when the returned state is
+`QHW_ADM_RESERVATION_ACTIVE`. QPM then compares user, job, device, scope, and
+policy metadata from the authenticated caller context and request payload with
+the reservation record and metadata stored in qhw-admission.
+
 This check belongs on every reservation-scoped execution path, including
 synchronous execution, asynchronous execution, cancellation that affects
 provider state, and any queued retry path that later submits work.
@@ -1067,6 +1175,14 @@
 large number of accepted qtasks from entering the scheduler when their combined
 estimated usage would exceed the reservation allowance.
 
+QPM maps that hold to qhw-admission usage calls. It builds a
+`qhw_adm_usage_t` with `reservation_id`, the QPM qtask ID in `task_id`, the
+estimated device time, credits, rate units, shot-derived baseline units, and
+policy metadata. `authorize_usage()` is an optional dry run used for status and
+delay guidance. `consume()` is the committed hold. Only an accepted `consume()`
+decision permits `qhw_sched_submit_task()`. A delayed or rejected decision keeps
+the qtask out of qhw-scheduler.
+
 </details>
 
 <details>
@@ -1082,6 +1198,11 @@
 QPM should include the qtask ID, QFw circuit ID, reservation ID, timing data,
 and provider result metadata needed to make usage records idempotent and
 auditable.
+
+Estimated usage is recorded by the accepted `consume()` call. Actual measured
+usage is recorded with `record_actual()` using `qhw_adm_actual_usage_t`. When a
+consumed estimate is not used, or only part of it is used, QPM calls
+`return_usage()` for the unused amount before or alongside final accounting.
 
 </details>
 
@@ -1231,11 +1352,17 @@
 ### ADM-017
 
 QPM should expose or enforce the reservation lifecycle states tracked by
-qhw-admission: active, renewed, released, cancelled, expired, and over-limit.
+qhw-admission: pending, active, released, expired, and cancelled.
 
 The lifecycle state should control whether new qtasks can be accepted, whether
 queued qtasks can remain pending, whether provider-side work must be cancelled,
 and how final usage and compliance records are emitted.
+
+`renew()` is a lifecycle operation, not a distinct qhw-admission state. A
+successful renew leaves the reservation active with an updated expiration.
+Over-limit is also not a qhw-admission reservation state. QPM reports
+over-limit conditions through `QHW_ADM_REASON_OVER_LIMIT`, usage state,
+`get_compliance()`, and structured QPM error or telemetry fields.
 
 </details>
 
@@ -1252,6 +1379,11 @@
 QPM should avoid split checks such as "read remaining capacity, then later
 record usage" without an admission-side concurrency guard.
 
+The concurrency guard is the `consume()` call on the shared admission context.
+QPM should never implement a separate capacity check with
+`get_usage()` or `get_capacity()` followed by scheduler insertion. Those reads
+are telemetry and diagnostics. They are not admission holds.
+
 </details>
 
 <details>
@@ -1266,6 +1398,11 @@
 This keeps the scheduler queue limited to work that is already authorized and
 covered by estimated capacity.
 
+In concrete terms, `qhw_sched_submit_task()` is called only after
+`consume(reservation_id, usage)` returns an accepted decision. If `consume()`
+returns delayed or rejected, QPM applies the ADM-020 policy outcome without
+creating a scheduler task ID.
+
 </details>
 
 <details>
@@ -1290,12 +1427,20 @@
 
 QPM should treat estimated qtask capacity as an in-flight hold. If the qtask is
 cancelled, rejected after the hold, fails before provider execution, or reaches
-any terminal state, QPM should release the hold and record final usage
+any terminal state, QPM should finalize the hold and record final usage
 according to qhw-admission policy.
 
 The completion callback into the controller should update scheduler state,
-release or consume the admission hold, record actual usage when available, and
-only then expose terminal results to clients.
+return unused consumed capacity, record actual usage when available, and only
+then expose terminal results to clients.
+
+The hold lifecycle maps to concrete usage operations. The accepted `consume()`
+decision creates the hold. `return_usage()` releases unused consumed capacity
+for cancellation, pre-execution failure, partial execution, and reconciled
+timeout cancellation. `record_actual()` stores measured execution feedback for
+completed or partially completed work. QPM must call these operations before
+publishing completion events, making `read_cq()` records visible, or returning
+a terminal `sync_run()` response.
 
 </details>
 
@@ -1310,8 +1455,10 @@
 the qtask obtains a hold and enters qhw-scheduler.
 
 Retry should also respond to reservation lifecycle changes. Released,
-cancelled, expired, or over-limit reservations should cause their pending
-qtasks to fail or cancel with a structured lifecycle reason.
+cancelled, or expired reservations should cause their pending qtasks to fail or
+cancel with a structured lifecycle reason. Over-limit conditions reported by
+usage or compliance state should apply the configured compliance action, such
+as delay, reject, throttle, terminate, or allow.
 
 </details>
 
@@ -1387,6 +1534,23 @@
 terminal state is exposed. That gives the controller a single place to update
 qhw-scheduler, qhw-admission, QPM result state, and events in the correct
 order.
+
+The concrete scheduler mapping is:
+
+| Lifecycle event | qhw-scheduler call and state |
+| --- | --- |
+| Scheduler insertion | `qhw_sched_submit_task()` creates `QHW_SCHED_TASK_QUEUED`, or `QHW_SCHED_TASK_WAITING` for a sliced parent. |
+| Scheduler selection | `qhw_sched_select_next()` returns an assignment and moves the task to `QHW_SCHED_TASK_ASSIGNED`. |
+| Provider accepted or started work | `qhw_sched_task_started()` moves the assigned task to `QHW_SCHED_TASK_RUNNING`. |
+| Successful provider completion | `qhw_sched_task_completed()` moves the task to `QHW_SCHED_TASK_COMPLETED`. |
+| Provider, dispatcher, or reconciliation failure | `qhw_sched_task_failed()` moves a non-terminal task to `QHW_SCHED_TASK_FAILED`. |
+| Cancellation after scheduler insertion | `qhw_sched_task_cancelled()` moves a non-terminal task to `QHW_SCHED_TASK_CANCELLED`. |
+| Synchronous wait timeout | No scheduler transition by default; QPM returns a timeout overlay that includes the current scheduler state. |
+
+Provider submission itself is QPM dispatcher state while the scheduler task is
+assigned. qhw-scheduler does not have a submitted state, so QPM must store the
+provider handle and submitted timestamp in runtime state and expose
+`SUBMITTED` as a managed-task status overlay.
 
 </details>
 
@@ -1499,6 +1663,11 @@
 
 Provider-specific states can be attached as metadata, but the public QPM status
 should normalize them into the managed-resource lifecycle.
+
+The status API derives visible state from the mapping in SCHED-005. Pending
+capacity has no qhw-scheduler task. Queued, selected, running, completed,
+failed, cancelled, and waiting are derived from `qhw_sched_task_get_state()`.
+Submitted and timed-out are QPM overlays from dispatcher and waiter state.
 
 </details>
 
@@ -1659,6 +1828,29 @@
 Compatibility can be handled by an explicit transition path, but the target
 production behavior should require the reservation ID.
 
+The Qiskit adapter migrates through the same API change. The current
+`qfw_lookup_service.get_qpm()` path should become a QPM resolver wrapper. In
+QFw-managed mode it keeps the existing `dirsvc.resolve_services("QPM", ...)` lookup
+and `defw.connect_to_binding()` binding. In long-running mode it reads the
+configured endpoint descriptor, connects directly to the DEFw-wrapped QPM, and
+validates readiness and metadata before returning the same QPM client binding.
+
+`QFwBackend.run()` should accept reservation context through backend options or
+run keyword arguments, including `reservation_id`, an execution token or
+trusted caller context, and an optional idempotency key. `QFwSamplerV2` and
+`QFwEstimatorV2` already pass backend run options through to `Backend.run()`;
+their migration should document the reservation fields and forward them without
+renaming. `QFwJob._run_experiment_async()` then includes those fields in the
+managed execution request and calls the reservation-scoped QPM execution API.
+
+The adapter must not treat DEFw-dirsvc service selection as a reservation.
+Reservation creation belongs to the QPM admission API and is normally performed
+by a trusted workflow manager, load manager, prolog, or site service before the
+application runs. A compatibility helper may request a reservation for trusted
+single-process examples, but production Qiskit execution should reject
+resource-affecting runs that lack a reservation ID and authenticated caller
+context.
+
 </details>
 
 <details>
@@ -1889,8 +2081,8 @@
 
 QPM cleanup should be tied to both execution terminal states and reservation
 lifecycle events. When a qtask completes, fails, is cancelled, or times out,
-QPM should release or consume its capacity hold, update scheduler state, record
-actual usage where available, emit final result or event state, and remove
+QPM should finalize its capacity hold, update scheduler state, record actual
+usage where available, emit final result or event state, and remove
 transient mappings that are no longer needed.
 
 When a reservation is released, cancelled, or expired, QPM should stop accepting
```

### Update After Review Pass 2

Recorded: 2026-07-30T18:09:11+00:00
Review event: agent-00047
Design author event: agent-00049
Review issue file: design-review.jsonl

#### Blocking Findings

- DR-P2-001: major open - Reservation release, cancel, and expiration cleanup ordering conflicts with current qhw-admission usage APIs.
- DR-P2-002: major open - Credential and trusted-transport authentication remain underspecified for reservation-scoped execution.

#### Changed Files

- docs/detailed-design.md

#### Design Author Summary

Updated [docs/detailed-design.md](/home/a2e/ORNL/Quantum/openQSE/QFw/docs/detailed-design.md) for design review pass 2.

Changes made:
- Addressed `DR-P2-001` by defining release, cancel, and TTL expiration as QPM close protocols. QPM now stops new work, reconciles held qtasks with `return_usage()` / `record_actual()` while the reservation is still `ACTIVE`, and only then calls `qhw_adm_release()`, `qhw_adm_cancel()`, or `qhw_adm_expire()`.
- Addressed `DR-P2-002` by specifying the QFw authorization envelope, trusted DEFw transport mapping, required credential claims, validation order, trust-root handling, replay/freshness policy, effective lifetime calculation, and structured auth error mapping.

Verification:
- `git -C QFw diff --check -- docs/detailed-design.md` passed.
- Non-table/code prose line-length scan passed.
- Did not edit `docs/design-review.md`, `docs/design-review-updates.md`, or `design-review.jsonl`.

```yaml
changed_files:
  - QFw/docs/detailed-design.md
  - QFw/.electroboy/shared/runs/2026-07-27T220849+0000-2452c04b/progress/design-review-update-progress.md
```

#### Detailed Design Diff

```diff
--- a/docs/detailed-design.md
+++ b/docs/detailed-design.md
@@ -390,9 +390,9 @@
     Client->>TaskAPI: async_run(info, reservation_id, token)
     TaskAPI->>QPM: DEFw RPC call
     QPM->>UTIL: submit reservation-scoped qtask
-    UTIL->>ADM: expire(now), get_reservation(reservation_id)
+    UTIL->>ADM: get_reservation(reservation_id)
     ADM-->>UTIL: reservation record or lifecycle error
-    UTIL->>UTIL: compare caller binding and request scope
+    UTIL->>UTIL: validate caller, scope, operation, and expiration
     UTIL->>ADM: authorize_usage(reservation_id, estimated usage)
     ADM-->>UTIL: accepted / delayed / rejected
     UTIL->>ADM: consume(reservation_id, estimated usage)
@@ -407,8 +407,8 @@
     QRC->>Provider: provider submission
     Provider-->>QRC: result or terminal provider state
     QRC-->>UTIL: completion callback or completion record
+    UTIL->>ADM: return_usage() and/or record_actual() while active
     UTIL->>SCHED: task_completed(), task_failed(), or task_cancelled()
-    UTIL->>ADM: return_usage() and/or record_actual()
     UTIL-->>TaskAPI: completion state
     TaskAPI-->>Client: event, read_cq(), status, or result response
 ```
@@ -435,6 +435,38 @@
 publishing the terminal result. Repeating a usage operation with the same
 nonzero task ID must use identical usage data so qhw-admission can apply its
 idempotency rules.
+
+Reservation lifecycle events use the same active-state accounting rule.
+qhw-admission usage APIs find an active reservation before accepting
+`consume()`, `return_usage()`, or `record_actual()`. QPM therefore treats
+release, cancel, and expiration as controller close requests rather than as
+immediate terminal-state calls into qhw-admission.
+
+The close protocol has a fixed order. QPM marks the reservation closing in its
+runtime map, rejects new resource-affecting work for that reservation, removes
+pending-capacity entries that never obtained a hold, and stops retries. It then
+drains, cancels, fails, or reconciles held qtasks according to the close reason
+and site policy. For every held qtask, QPM calls `return_usage()` for unused
+estimated capacity and `record_actual()` for known measured usage while
+`get_reservation()` still reports `QHW_ADM_RESERVATION_ACTIVE`. Only after all
+held qtasks have final accounting does QPM call `qhw_adm_release()` or
+`qhw_adm_cancel()`.
+
+TTL expiration follows a reservation sweep with the same ordering. The sweep
+collects active reservations whose `expires_at_ns` is at or before the
+controller time, closes each expired reservation's local work while the
+reservation remains active, and then invokes `qhw_adm_expire(now)` only after
+every expired reservation in the sweep has no unreconciled held qtasks. A
+request path that finds an active but expired reservation starts this close
+protocol and returns an expired-reservation status. It does not call
+`qhw_adm_expire()` before usage reconciliation.
+
+If QPM discovers unreconciled held work after a reservation is already
+`RELEASED`, `CANCELLED`, or `EXPIRED`, current qhw-admission cannot accept
+normal final accounting for that work. QPM reports a reconciliation fault and
+operator-visible audit record. A future qhw-admission extension may add
+terminal-state final-accounting APIs, but this design works with the existing
+active-only usage contract.
 
 Managed task status is a QPM-facing view over QPM pending state,
 qhw-scheduler task state, dispatcher state, and provider state:
@@ -688,8 +720,8 @@
 | `prepare_circuit(info)` | Create and provider-decorate a QFw circuit record before admission and scheduling. Existing `create_circuit()` overrides can migrate here. |
 | `prepare_provider_submission(circuit)` | Apply provider-specific launch metadata after capacity has been held and before scheduler insertion or dispatch. QB can use this for vQPU configuration. |
 | `submit_scheduled_circuit(circuit, mode)` | Submit only scheduler-selected work to QRC for synchronous or asynchronous execution. |
-| `complete_scheduled_circuit(cid, result)` | Update scheduler lifecycle state, return unused consumed capacity, record actual usage, and publish result state. |
-| `cancel_scheduled_circuit(cid, reservation_id, reason)` | Propagate cancellation through pending state, scheduler state, provider handles, result state, and admission accounting. |
+| `complete_scheduled_circuit(cid, result)` | Return unused consumed capacity, record actual usage, update scheduler lifecycle state, and publish result state. |
+| `cancel_scheduled_circuit(cid, reservation_id, reason)` | Propagate cancellation through pending state, scheduler state, provider handles, active-state admission accounting, and result state. |
 
 ### QFw API Categories
 
@@ -709,8 +741,85 @@
 trusted workflow or load manager. They return a reservation ID and, when the
 deployment uses bearer-style credentials, a bounded execution token tied to the
 reservation. Applications and runtimes submit qtasks through execution APIs
-using that reservation ID and token. Site operators configure admission policy
-through the admission policy configuration surface.
+using that reservation ID and authorization envelope. Site operators configure
+admission policy through the admission policy configuration surface.
+
+#### Authentication Context
+
+The `token` parameter in the API tables is a QFw authorization envelope. It is
+not the reservation ID and is separate from provider API keys used by QRMI,
+QDMI, IQM, QB, or other backend drivers. A deployment may also authorize a call
+without a serialized token when the request arrives over a site-configured
+trusted DEFw transport binding that QPM can map to the same normalized caller
+context.
+
+QPM accepts two caller-context sources:
+
+| Source | Validation authority | Normalized context |
+| --- | --- | --- |
+| Signed QFw reservation credential | QPM trust-root configuration keyed by issuer and key ID. | User or service principal, reservation ID, device ID, scope, job or allocation ID, operation set, issue time, freshness ID, and expiration. |
+| Trusted DEFw transport binding | Site configuration that binds DEFw source endpoint, `remote_uuid`, `blk_uuid`, hostname, process attributes, or launcher metadata to a principal. | The same caller-context fields produced from a credential. |
+
+DEFw connection UUIDs and block UUIDs are transport correlation handles. They
+become authentication evidence only when the site explicitly places the source
+endpoint in the trusted-transport map and associates it with a principal and
+allowed API category. Public application calls should use a signed reservation
+credential unless they run inside such a trusted launch context.
+
+The QFw reservation credential envelope is versioned and signed. Its required
+claims are:
+
+| Claim | Meaning |
+| --- | --- |
+| `typ` and `version` | Credential type `qfw.reservation` and envelope version. |
+| `iss` and `kid` | Issuer and signing-key identifier resolved from QPM trust roots. |
+| `aud` | QPM service ID, device ID, or audience accepted by the target service. |
+| `sub` | User ID or service principal allowed to use the reservation. |
+| `reservation_id` | Reservation being used by the request. |
+| `device_id` and `scope_id` | Target device and admission scope. |
+| `job_id` or `allocation_id` | Scheduler or workflow binding when site policy requires it. |
+| `operations` | Allowed operations, such as execute, cancel, status, result, or telemetry. |
+| `iat`, `nbf`, and `exp` | Issue time, not-before time, and credential expiration. |
+| `jti` | Freshness identifier used for replay detection. |
+| `cnf` | Optional confirmation binding to a DEFw transport or launcher session. |
+
+The service API layer validates authentication before calling the controller:
+
+1. Parse the credential envelope, or derive candidate context from the trusted
+   DEFw transport binding when no credential is supplied.
+2. Verify the credential type, signature or MAC, issuer, key ID, and audience
+   against QPM trust-root configuration.
+3. Check `nbf`, `iat`, `exp`, and the configured clock-skew allowance.
+4. Enforce freshness by rejecting replayed `jti` values inside the replay
+   window, except for explicitly idempotent calls with the same idempotency key
+   and identical request digest.
+5. Compare any `cnf` transport binding with the live DEFw source endpoint,
+   `remote_uuid`, `blk_uuid`, and launcher metadata.
+6. Normalize the result into a `QFwCallerContext` and fetch the reservation
+   record from qhw-admission.
+7. Compute the effective authorization expiration as the minimum of credential
+   expiration, trusted transport session expiration, launcher allocation
+   expiration when present, and reservation expiration.
+8. Compare the normalized context with the reservation binding and requested
+   operation before any usage hold, scheduler insertion, provider submission, or
+   terminal task publication.
+
+The normalized `QFwCallerContext` contains `principal_type`,
+`principal_id`, `issuer`, `trust_root_id`, `reservation_id`, `device_id`,
+`scope_id`, optional `job_id`, optional `allocation_id`, optional
+`project_id`, optional `session_id`, allowed operations, authenticated source,
+freshness ID, effective expiration, and the request digest used for
+idempotency.
+
+Structured errors distinguish authentication and authorization failures:
+
+| Failure | QPM reason |
+| --- | --- |
+| Missing credential and no trusted transport mapping | `UNAUTHENTICATED` |
+| Malformed envelope, invalid signature, unknown issuer, bad audience, or unsupported type | `INVALID_CREDENTIAL` |
+| Not-before violation, stale issue time, replayed freshness ID, or mismatched idempotency digest | `STALE_CREDENTIAL` |
+| Credential, trusted session, launcher allocation, or reservation lifetime expired | `EXPIRED_CREDENTIAL` or `EXPIRED_RESERVATION` as applicable |
+| Existing reservation with non-matching principal, job, allocation, device, scope, or operation | `UNAUTHORIZED_CALLER` |
 
 #### Admission Policy Configuration APIs
 
@@ -753,8 +862,8 @@
 | `evaluate(token, request)` | `token`; request ID; user ID; job or allocation ID when applicable; target `device_id`; `scope_id`; workload kind; circuit, shot, walltime, or device-time estimate; expiration or TTL; policy metadata. | Returns accepted, delayed, or rejected with machine-readable reason and estimate context. |
 | `reserve(token, request)` | Same request fields as `evaluate()`, with caller authority to bind ownership. | Creates an accepted reservation and returns `reservation_id`, lifecycle state, expiration, and optional execution token. |
 | `renew(token, reservation_id, expiration_or_ttl)` | `token`, `reservation_id`, new expiration or TTL. | Extends reservation lifetime when policy permits it. |
-| `release(token, reservation_id, reason)` | `token`, `reservation_id`, optional reason. | Releases unused reservation capacity and stops new work under the reservation. |
-| `cancel(token, reservation_id, reason)` | `token`, `reservation_id`, cancellation reason. | Cancels reservation-scoped pending and queued work according to site policy. |
+| `release(token, reservation_id, reason)` | `token`, `reservation_id`, optional reason. | Starts the QPM close protocol, stops new work, finalizes held-task accounting, and then moves the reservation to released. |
+| `cancel(token, reservation_id, reason)` | `token`, `reservation_id`, cancellation reason. | Starts the QPM close protocol, cancels or fails reservation-scoped work according to site policy, finalizes held-task accounting, and then moves the reservation to cancelled. |
 | `get_reservation(token, reservation_id)` | `token`, `reservation_id`. | Returns reservation state, owner binding, expiration, allowance, and usage summary filtered by caller authorization. |
 | `list_reservations(token, filters)` | `token`, device, owner, job, state, or time filters. | Returns reservation summaries visible to the caller. |
 
@@ -762,25 +871,26 @@
 
 Execution APIs replace the resource-affecting subset of the current
 `api_qpm.QPM` execution surface. Applications and runtimes call these APIs with
-a reservation ID and authenticated caller token. The API contract is expressed
-as a managed task lifecycle so status, cancellation, result retrieval, and
-events use the same state vocabulary.
+a reservation ID and a QFw authorization envelope, or through a configured
+trusted transport that yields the same caller context. The API contract is
+expressed as a managed task lifecycle so status, cancellation, result
+retrieval, and events use the same state vocabulary.
 
 | API | Parameters | Result |
 | --- | --- | --- |
-| `sync_run(info, reservation_id, token, timeout_s, cancel_on_timeout, idempotency_key)` | Current circuit `info`; `reservation_id`; caller token; optional timeout, timeout-cancellation policy, and idempotency key. | Runs through admission and scheduler, then blocks until a terminal result or returns structured timeout, delayed, cancelled, or failure status. |
-| `async_run(info, reservation_id, token, idempotency_key)` | Current circuit `info`; `reservation_id`; caller token; optional idempotency key. | Returns QFw circuit ID, qtask ID, scheduler task ID when available, and managed lifecycle status. |
-| `cancel_task(cid, reservation_id, token, reason)` | QFw circuit or qtask ID; `reservation_id`; caller token; optional reason. | Cancels pending, queued, selected, or provider-submitted work and updates admission accounting. |
-| `task_status(cid, reservation_id, token)` | QFw circuit or qtask ID; `reservation_id`; caller token. | Returns pending, queued, selected, submitted, running, completed, failed, cancelled, or timed-out state. |
-| `read_cq(cid, reservation_id, token)` | Optional circuit ID; optional reservation ID for scoped reads; caller token. | Returns and removes a visible completion record or a structured in-progress status. |
-| `peek_cq(cid, reservation_id, token)` | Optional circuit ID; optional reservation ID for scoped reads; caller token. | Returns a visible completion record without removing it. |
-| `register_event_notification(ep, evtype, class_id, token, reservation_id, filters)` | Event endpoint, event type, class ID, caller token, optional reservation scope and filters. | Registers event delivery for authorized task lifecycle events. |
-| `delete_circuit(cid, reservation_id, token)` | Circuit ID; reservation ID when reservation-scoped; caller token. | Removes client-visible circuit state when lifecycle and retention policy allow it. |
+| `sync_run(info, reservation_id, token, timeout_s, cancel_on_timeout, idempotency_key)` | Current circuit `info`; `reservation_id`; QFw authorization envelope; optional timeout, timeout-cancellation policy, and idempotency key. | Runs through admission and scheduler, then blocks until a terminal result or returns structured timeout, delayed, cancelled, or failure status. |
+| `async_run(info, reservation_id, token, idempotency_key)` | Current circuit `info`; `reservation_id`; QFw authorization envelope; optional idempotency key. | Returns QFw circuit ID, qtask ID, scheduler task ID when available, and managed lifecycle status. |
+| `cancel_task(cid, reservation_id, token, reason)` | QFw circuit or qtask ID; `reservation_id`; QFw authorization envelope; optional reason. | Cancels pending, queued, selected, or provider-submitted work and updates admission accounting. |
+| `task_status(cid, reservation_id, token)` | QFw circuit or qtask ID; `reservation_id`; QFw authorization envelope. | Returns pending, queued, selected, submitted, running, completed, failed, cancelled, or timed-out state. |
+| `read_cq(cid, reservation_id, token)` | Optional circuit ID; optional reservation ID for scoped reads; QFw authorization envelope. | Returns and removes a visible completion record or a structured in-progress status. |
+| `peek_cq(cid, reservation_id, token)` | Optional circuit ID; optional reservation ID for scoped reads; QFw authorization envelope. | Returns a visible completion record without removing it. |
+| `register_event_notification(ep, evtype, class_id, token, reservation_id, filters)` | Event endpoint, event type, class ID, QFw authorization envelope, optional reservation scope and filters. | Registers event delivery for authorized task lifecycle events. |
+| `delete_circuit(cid, reservation_id, token)` | Circuit ID; reservation ID when reservation-scoped; QFw authorization envelope. | Removes client-visible circuit state when lifecycle and retention policy allow it. |
 
 #### Synchronous Execution Contract
 
 `sync_run()` uses the same controller path as `async_run()`. It validates the
-reservation and caller token, creates the QFw circuit and managed qtask,
+reservation and caller context, creates the QFw circuit and managed qtask,
 establishes the admission hold, inserts the task into qhw-scheduler, and waits
 on the managed task lifecycle.
 
@@ -835,11 +945,11 @@
 | `get_dynamic_backend_info(calibration_set_id, lib, token)` | Optional calibration set, library selector, and token. | Returns dynamic backend metadata. |
 | `get_calibration_snapshot(calibration_set_id, lib, token)` | Optional calibration set, library selector, and token. | Returns calibration data filtered by site policy. |
 | `get_coupling_graph(calibration_set_id, lib, token)` | Optional calibration set, library selector, and token. | Returns topology data visible to the caller. |
-| `get_last_job_timing(cid, lib, reservation_id, token)` | Optional circuit ID, library selector, reservation ID, and caller token. | Returns timing data for caller-owned work or aggregate views authorized by policy. |
-| `get_last_job_metadata(cid, lib, reservation_id, token)` | Optional circuit ID, library selector, reservation ID, and caller token. | Returns provider and QPM metadata visible to the caller. |
-| `get_capacity_snapshot(token, device_id, scope_id)` | Caller token, device ID, optional scope. | Returns admission capacity, held capacity, active reservations, and confidence values visible to the caller. |
-| `get_queue_metrics(token, device_id, access_class)` | Caller token, device ID, requested access class. | Returns pending count, scheduler depth, estimated queued device time, active task count, and policy-specific metrics as authorized. |
-| `get_task_metadata(token, cid, reservation_id)` | Caller token, circuit or qtask ID, optional reservation ID. | Returns managed-resource lifecycle metadata for visible work. |
+| `get_last_job_timing(cid, lib, reservation_id, token)` | Optional circuit ID, library selector, reservation ID, and QFw authorization envelope. | Returns timing data for caller-owned work or aggregate views authorized by policy. |
+| `get_last_job_metadata(cid, lib, reservation_id, token)` | Optional circuit ID, library selector, reservation ID, and QFw authorization envelope. | Returns provider and QPM metadata visible to the caller. |
+| `get_capacity_snapshot(token, device_id, scope_id)` | QFw authorization envelope, device ID, optional scope. | Returns admission capacity, held capacity, active reservations, and confidence values visible to the caller. |
+| `get_queue_metrics(token, device_id, access_class)` | QFw authorization envelope, device ID, requested access class. | Returns pending count, scheduler depth, estimated queued device time, active task count, and policy-specific metrics as authorized. |
+| `get_task_metadata(token, cid, reservation_id)` | QFw authorization envelope, circuit or qtask ID, optional reservation ID. | Returns managed-resource lifecycle metadata for visible work. |
 
 ### Integration Sequence
 
@@ -1116,8 +1226,16 @@
 ### ADM-003
 
 QPM should expose a release API that accepts a reservation ID and reason code
-when available. It should call qhw-admission `release()` and then clean local
-transient execution state associated with that reservation.
+when available. It should run the QPM close protocol for that reservation and
+then call qhw-admission `release()` after held-task accounting has been
+finalized while the reservation is still active.
+
+During release, QPM marks the reservation closing in runtime state, stops new
+work and pending retries, removes pending entries that never consumed
+capacity, and drains or cancels held qtasks according to site policy. Each held
+qtask is reconciled with `return_usage()` for unused estimated capacity and
+`record_actual()` when measured usage is known. The terminal
+`qhw_adm_release()` call is the last admission operation for the reservation.
 
 Release is an admission lifecycle operation, not a DEFw-dirsvc deregistration
 operation.
@@ -1146,11 +1264,15 @@
 cancelled, and matches the requested job, session, scope, target device, and
 operation type.
 
-The concrete check is a controller sequence. QPM first calls
-`qhw_adm_expire()` or the Python `expire()` wrapper with the controller's
-current time policy, then calls `get_reservation(reservation_id)`. The
-reservation is usable only when the returned state is
-`QHW_ADM_RESERVATION_ACTIVE`. QPM then compares user, job, device, scope, and
+The concrete check is a controller sequence. QPM calls
+`get_reservation(reservation_id)` and requires the returned state to be
+`QHW_ADM_RESERVATION_ACTIVE`. It compares the controller time with
+`expires_at_ns` before permitting work. When an active reservation is past its
+expiration, QPM starts the expiration close protocol and returns an
+expired-reservation status. The request path does not call `qhw_adm_expire()`
+before held-task usage has been reconciled.
+
+For usable reservations, QPM compares user, job, device, scope, operation, and
 policy metadata from the authenticated caller context and request payload with
 the reservation record and metadata stored in qhw-admission.
 
@@ -1251,6 +1373,11 @@
 carry authenticated caller context, or arrive over a trusted transport that
 QFw can map to authenticated caller context.
 
+The `token` API argument is the serialized QFw authorization envelope described
+in the API authentication context. A trusted transport request may omit that
+envelope only when QPM has a site-configured mapping from the live DEFw source
+endpoint to the same normalized caller-context fields.
+
 </details>
 
 <details>
@@ -1265,6 +1392,12 @@
 The service API layer should perform the credential and transport checks before
 passing a normalized caller context to the controller. The controller should not
 make authorization decisions from raw untrusted client fields.
+
+Credential validation occurs before reservation validation. QPM verifies the
+envelope type, signature or MAC, issuer, key ID, audience, operation set,
+freshness ID, and time bounds. For trusted transport, QPM verifies the live DEFw
+source endpoint, `remote_uuid`, `blk_uuid`, hostname, launcher metadata, and
+configured principal binding before accepting the derived caller context.
 
 </details>
 
@@ -1282,6 +1415,13 @@
 where a site intentionally allows groups, delegated identities, or workflow
 service identities.
 
+The normalized context fields are `principal_type`, `principal_id`, `issuer`,
+`trust_root_id`, `reservation_id`, `device_id`, `scope_id`, optional `job_id`,
+optional `allocation_id`, optional `project_id`, optional `session_id`, allowed
+operations, authenticated source, freshness ID, effective expiration, and
+request digest. The reservation ID, device ID, scope ID, and requested operation
+must match the reservation binding and the RPC request.
+
 </details>
 
 <details>
@@ -1312,6 +1452,10 @@
 credential lifetime, the trusted transport session lifetime, and the
 reservation expiration.
 
+When launcher allocation metadata is present, the allocation lifetime also
+bounds the effective authorization lifetime. A request with `now` at or beyond
+that effective expiration fails before any usage hold or scheduler insertion.
+
 </details>
 
 <details>
@@ -1326,6 +1470,17 @@
 
 Invalid or stale credentials should fail authorization before QPM calls
 qhw-admission for capacity holds or qhw-scheduler for task insertion.
+
+Trust roots are loaded from QPM service configuration and are scoped by issuer,
+key ID, accepted audience, and API category. QPM keeps replay-cache entries for
+credential `jti` values until the effective authorization expiration plus the
+configured skew window. Idempotent retries may reuse a `jti` only when the
+idempotency key and request digest match the original accepted request.
+
+Structured failures should distinguish missing authentication, invalid
+credential material, stale or replayed credentials, expired credentials,
+expired reservations, and authenticated callers that do not match the stored
+reservation binding.
 
 </details>
 
@@ -1430,8 +1585,8 @@
 any terminal state, QPM should finalize the hold and record final usage
 according to qhw-admission policy.
 
-The completion callback into the controller should update scheduler state,
-return unused consumed capacity, record actual usage when available, and only
+The completion callback into the controller should return unused consumed
+capacity, record actual usage when available, update scheduler state, and only
 then expose terminal results to clients.
 
 The hold lifecycle maps to concrete usage operations. The accepted `consume()`
@@ -1442,6 +1597,13 @@
 publishing completion events, making `read_cq()` records visible, or returning
 a terminal `sync_run()` response.
 
+Reservation close events follow the same rule. Release, cancel, and expiration
+requests finalize every held qtask with `return_usage()` and `record_actual()`
+while qhw-admission still reports the reservation as active. The QPM close
+protocol calls `qhw_adm_release()`, `qhw_adm_cancel()`, or `qhw_adm_expire()`
+only after those active-state accounting calls have finished or after QPM has
+reported a reconciliation fault.
+
 </details>
 
 <details>
@@ -1459,6 +1621,11 @@
 cancel with a structured lifecycle reason. Over-limit conditions reported by
 usage or compliance state should apply the configured compliance action, such
 as delay, reject, throttle, terminate, or allow.
+
+The lifecycle change is observed through the controller close state before the
+qhw-admission reservation enters a terminal state. Pending qtasks without a
+consumed hold can be removed immediately. Held qtasks stay in the close set
+until their final accounting is recorded.
 
 </details>
 
@@ -1566,6 +1733,12 @@
 reservation inspection agree on whether usage was consumed, returned, or
 recorded as actual device time.
 
+For reservation-level release, cancel, and expiration events, QPM also updates
+qhw-admission usage before invoking the terminal reservation transition. This
+keeps active-only calls such as `return_usage()` and `record_actual()` valid
+while still ensuring that clients never observe a terminal task result before
+admission accounting is final.
+
 </details>
 
 <details>
@@ -2081,8 +2254,8 @@
 
 QPM cleanup should be tied to both execution terminal states and reservation
 lifecycle events. When a qtask completes, fails, is cancelled, or times out,
-QPM should finalize its capacity hold, update scheduler state, record actual
-usage where available, emit final result or event state, and remove
+QPM should finalize its capacity hold, record actual usage where available,
+update scheduler state, emit final result or event state, and remove
 transient mappings that are no longer needed.
 
 When a reservation is released, cancelled, or expired, QPM should stop accepting
@@ -2090,6 +2263,12 @@
 according to policy, reconcile provider-side work, and clean local transient
 state when it is safe to do so.
 
-</details>
-
-</details>
+The reservation terminal transition is the final step of cleanup. QPM keeps the
+reservation active in qhw-admission while it drains or cancels in-flight held
+qtasks and records final usage. After the active close set is empty, QPM calls
+the matching qhw-admission lifecycle API and removes transient mappings whose
+retention windows have elapsed.
+
+</details>
+
+</details>
```

### Update After Review Pass 1

Recorded: 2026-07-30T21:02:08+00:00
Review event: agent-00057
Design author event: agent-00059
Review issue file: design-review.jsonl

#### Blocking Findings

- DR-P4-001: major open - Qiskit Estimator reservation context forwarding is assumed but not supported by the current code.

#### Changed Files

- docs/detailed-design.md

#### Design Author Summary

Updated [docs/detailed-design.md](/home/a2e/ORNL/Quantum/openQSE/QFw/docs/detailed-design.md:69) for `DR-P4-001`.

Design changes made:
- Added current-code context showing `QFwBackend.run()` and `QFwJob` do not forward reservation context today.
- Corrected `API-001` so it no longer assumes `QFwEstimatorV2` has backend option pass-through.
- Specified the required Estimator migration: add `run_options` or an execution-context option and forward reservation fields to every derived measurement circuit.

Verification: `git diff --check -- docs/detailed-design.md` passed. No code tests were run because this was documentation-only.

changed_files:
- `docs/detailed-design.md`
- `.electroboy/shared/runs/2026-07-27T220849+0000-2452c04b/progress/design-review-update-progress.md`

#### Detailed Design Diff

```diff
--- a/docs/detailed-design.md
+++ b/docs/detailed-design.md
@@ -66,6 +66,13 @@
   delegate hardware operations to a provider-specific QRC object.
 - QRC owns the current provider execution mechanics, including asynchronous
   Python workers, provider calls, completion records, and callback delivery.
+- `backends/qfw_qiskit/qfw_simulator.py` currently keeps only `shots`, `seed`,
+  and `seed_simulator` when constructing a `QFwJob`.
+- `backends/qfw_qiskit/qfw_job.py` currently calls `qpm.async_run(info)`
+  without reservation context.
+- `backends/qfw_qiskit/qfw_sampler.py` exposes `Options.run_options`, while
+  `backends/qfw_qiskit/qfw_estimator.py` has no equivalent backend pass-through
+  option.
 
 </details>
 
@@ -2037,11 +2044,21 @@
 
 `QFwBackend.run()` should accept reservation context through backend options or
 run keyword arguments, including `reservation_id`, an execution token or
-trusted caller context. `QFwSamplerV2` and `QFwEstimatorV2` already pass
-backend run options through to `Backend.run()`; their migration should document
-the reservation fields and forward them without renaming.
-`QFwJob._run_experiment_async()` then includes those fields in the managed
+trusted caller context, and an optional idempotency key. The method should copy
+those values into `QFwJob` options instead of dropping them as unused kwargs.
+`QFwJob._run_experiment_async()` then places those fields in the managed
 execution request and calls the reservation-scoped QPM execution API.
+
+The primitive wrappers need different migration steps. `QFwSamplerV2` already
+has `Options.run_options` and forwards that dictionary when it invokes the
+backend. `QFwEstimatorV2` does not. Its `Options` dataclass exposes default
+precision, grouping, and simulator seed, and `_run_pubs()` calls
+`_run_circuits()` with only shots and seed. The Estimator migration should add
+a matching `run_options` or reserved execution-context option, merge it into
+each backend call without renaming reservation fields, and preserve the same
+context for every derived measurement circuit generated from an Estimator PUB.
+Until that pass-through exists, Estimator submissions are incompatible with
+reservation-scoped production execution.
 
 The adapter must not treat DEFw-dirsvc service selection as a reservation.
 Reservation creation belongs to the QPM admission API and is normally performed
```

### Update After Review Pass 2

Recorded: 2026-07-30T21:13:10+00:00
Review event: agent-00061
Design author event: agent-00063
Review issue file: design-review.jsonl

#### Blocking Findings

- DR-P5-001: major open - Pending-capacity retry path conflicts with qhw-admission consume idempotency.

#### Changed Files

- docs/detailed-design.md

#### Design Author Summary

Updated `QFw/docs/detailed-design.md` for DR-P5-001.

Design changes made:
- Pending-capacity retry now uses `authorize_usage()` as the repeatable dry-run probe.
- `consume()` is now described as a one-way commit called only after accepted authorization and immediately before scheduler insertion.
- Delayed or rejected `consume()` results are no longer retryable pending states for the same qtask ID.
- The sequence diagram, QPM status table, ADM-006, ADM-019, ADM-020, ADM-022, and SCHED-004 now use the same retry/commit semantics.

Why: current `qhw-admission` records `consume()` decisions under nonzero `task_id`, so retrying delayed `consume()` with the same qtask ID cannot later become an accepted hold without violating the idempotency model.

Verification: `git -C QFw diff --check -- docs/detailed-design.md` passed.

```yaml
changed_files:
  - QFw/docs/detailed-design.md
  - QFw/.electroboy/shared/runs/2026-07-27T220849+0000-2452c04b/progress/design-review-update-progress.md
```

#### Detailed Design Diff

```diff
--- a/docs/detailed-design.md
+++ b/docs/detailed-design.md
@@ -402,11 +402,18 @@
     UTIL->>UTIL: validate caller, scope, operation, and expiration
     UTIL->>ADM: authorize_usage(reservation_id, estimated usage)
     ADM-->>UTIL: accepted / delayed / rejected
-    UTIL->>ADM: consume(reservation_id, estimated usage)
-    ADM-->>UTIL: accepted hold / delayed / rejected
-    UTIL->>SCHED: submit_task(qtask descriptor)
-    SCHED-->>UTIL: scheduler task id
-    UTIL-->>Client: qtask id and queued status
+    alt authorization accepted
+        UTIL->>ADM: consume(reservation_id, estimated usage)
+        ADM-->>UTIL: accepted hold
+        UTIL->>SCHED: submit_task(qtask descriptor)
+        SCHED-->>UTIL: scheduler task id
+        UTIL-->>Client: qtask id and queued status
+    else authorization delayed and site policy queues
+        UTIL->>UTIL: keep qtask pending without usage event
+        UTIL-->>Client: qtask id and pending-capacity status
+    else authorization rejected
+        UTIL-->>Client: structured admission rejection
+    end
 
     UTIL->>SCHED: select_next() when dispatch slot opens
     SCHED-->>UTIL: selected qtask
@@ -431,17 +438,32 @@
 
 The qhw-admission call sequence uses `qhw_adm_usage_t.task_id` as the stable
 key for estimated usage operations. QPM fills that field with the QPM qtask ID
-and stores the matching QFw circuit ID and scheduler task ID in its runtime
-mapping. The estimated capacity hold is the accepted `consume()` decision. A
-qtask that cannot obtain an accepted `consume()` decision remains out of
-qhw-scheduler and either fails, delays, or enters the QPM pending-capacity
-queue according to site policy. If a consumed qtask is cancelled before
-provider execution, fails before execution, or uses less than the charged
-estimate, QPM calls `return_usage()` once for the unused amount. After provider
-completion, QPM calls `record_actual()` with `qhw_adm_actual_usage_t` before
-publishing the terminal result. Repeating a usage operation with the same
-nonzero task ID must use identical usage data so qhw-admission can apply its
-duplicate-usage rules.
+and stores the matching QFw circuit ID and scheduler task ID, once available,
+in its runtime mapping. The same estimated usage payload is used for dry-run
+authorization and commit.
+
+`authorize_usage()` is the retryable capacity probe. It does not create a
+usage event, so QPM may repeat it for a pending qtask with the same nonzero
+task ID and identical usage data as capacity changes. A qtask enters the
+QPM pending-capacity queue only after `authorize_usage()` returns delayed and
+site policy chooses queueing.
+
+`consume()` is the one-way commit that creates the estimated hold. For a
+nonzero task ID, qhw-admission stores the consume decision under that key and
+returns the stored decision on repeated calls with identical usage. QPM
+therefore calls `consume()` only after an accepted authorization decision and
+only when it is ready to submit the qtask to qhw-scheduler. A delayed or
+rejected `consume()` result is a commit failure for that qtask rather than a
+retryable pending-capacity state. QPM must not retry `consume()` with the same
+qtask ID, and it must not switch to a different qtask ID for the same logical
+qtask to bypass admission idempotency.
+
+If a consumed qtask is cancelled before provider execution, fails before
+execution, or uses less than the charged estimate, QPM calls `return_usage()`
+once for the unused amount. After provider completion, QPM calls
+`record_actual()` with `qhw_adm_actual_usage_t` before publishing the terminal
+result. Repeating a usage operation with the same nonzero task ID must use
+identical usage data so qhw-admission can apply its duplicate-usage rules.
 
 Reservation lifecycle events use the same active-state accounting rule.
 qhw-admission usage APIs find an active reservation before accepting
@@ -480,7 +502,7 @@
 
 | QPM status | Concrete scheduler state or owner | Required transition |
 | --- | --- | --- |
-| `PENDING_CAPACITY` | QPM pending queue; no scheduler task exists. | Entered after `authorize_usage()` or `consume()` returns delayed and site policy queues the qtask. |
+| `PENDING_CAPACITY` | QPM pending queue; no scheduler task exists. | Entered after `authorize_usage()` returns delayed and site policy queues the qtask without calling `consume()`. |
 | `QUEUED` | `QHW_SCHED_TASK_QUEUED`. | Entered after accepted `consume()` and successful `qhw_sched_submit_task()`. |
 | `WAITING` | `QHW_SCHED_TASK_WAITING`. | Used for a sliced parent while child qtasks are queued or running. |
 | `SELECTED` | `QHW_SCHED_TASK_ASSIGNED`. | Entered after `qhw_sched_select_next()` returns the assignment. |
@@ -1256,10 +1278,10 @@
 finalized while the reservation is still active.
 
 During release, QPM marks the reservation closing in runtime state, stops new
-work and pending retries, removes pending entries that never consumed
-capacity, and drains or cancels held qtasks according to site policy. Each held
-qtask is reconciled with `return_usage()` for unused estimated capacity and
-`record_actual()` when measured usage is known. The terminal
+work and pending retries, removes pending entries that never obtained a
+committed hold, and drains or cancels held qtasks according to site policy.
+Each held qtask is reconciled with `return_usage()` for unused estimated
+capacity and `record_actual()` when measured usage is known. The terminal
 `qhw_adm_release()` call is the last admission operation for the reservation.
 
 Release is an admission lifecycle operation, not a DEFw-dirsvc deregistration
@@ -1325,10 +1347,16 @@
 QPM maps that hold to qhw-admission usage calls. It builds a
 `qhw_adm_usage_t` with `reservation_id`, the QPM qtask ID in `task_id`, the
 estimated device time, credits, rate units, shot-derived baseline units, and
-policy metadata. `authorize_usage()` is an optional dry run used for status and
-delay guidance. `consume()` is the committed hold. Only an accepted `consume()`
-decision permits `qhw_sched_submit_task()`. A delayed or rejected decision keeps
-the qtask out of qhw-scheduler.
+policy metadata. `authorize_usage()` is the dry run used for initial admission
+guidance and pending-capacity retry. A delayed authorization decision can be
+rechecked later because it does not create a qhw-admission usage event.
+
+`consume()` is the committed hold. Only an accepted `consume()` decision permits
+`qhw_sched_submit_task()`. Because qhw-admission stores consume decisions for
+nonzero task IDs, QPM calls `consume()` only after an accepted authorization
+decision and only when the qtask is ready to enter qhw-scheduler. A delayed or
+rejected consume decision keeps the qtask out of qhw-scheduler, but it is not a
+retryable pending-capacity state for the same qtask ID.
 
 </details>
 
@@ -1588,9 +1616,13 @@
 covered by estimated capacity.
 
 In concrete terms, `qhw_sched_submit_task()` is called only after
-`consume(reservation_id, usage)` returns an accepted decision. If `consume()`
+`authorize_usage(reservation_id, usage)` returns an accepted decision and
+`consume(reservation_id, usage)` commits the hold. If `authorize_usage()`
 returns delayed or rejected, QPM applies the ADM-020 policy outcome without
-creating a scheduler task ID.
+creating a scheduler task ID or qhw-admission usage event. If `consume()`
+returns delayed or rejected during commit, QPM reports a structured admission
+commit failure for that qtask instead of placing the same task ID into the
+pending-capacity retry queue.
 
 </details>
 
@@ -1606,6 +1638,13 @@
 Pending qtasks have not entered qhw-scheduler. They remain under QPM control
 until capacity becomes available, the request times out, the caller cancels the
 task, or the reservation lifecycle prevents further work.
+
+A pending-capacity entry stores the stable QPM qtask ID, the exact estimated
+usage payload, caller binding, timeout, and cancellation state. It does not
+represent a qhw-admission usage event. While the entry is pending, QPM retries
+`authorize_usage()` with the same task ID and identical usage data. QPM calls
+`consume()` only when an authorization retry is accepted and the controller is
+ready to commit the hold and submit the qtask to qhw-scheduler.
 
 </details>
 
@@ -1646,9 +1685,18 @@
 ### ADM-022
 
 When held capacity is released or additional reservation capacity becomes
-available, QPM should retry pending qtasks for the affected reservation. Retry
-ordering should follow site policy and should not bypass scheduler policy once
-the qtask obtains a hold and enters qhw-scheduler.
+available, QPM should retry pending qtasks for the affected reservation.
+Pending retry means rechecking `authorize_usage()`, not repeating `consume()`.
+Retry ordering should follow site policy and should not bypass scheduler policy
+once the qtask obtains a hold and enters qhw-scheduler.
+
+When a pending retry receives an accepted authorization decision, QPM commits
+the hold with `consume()` using the same qtask ID and identical usage payload.
+After `consume()` accepts the hold, QPM removes the pending entry and inserts
+the qtask into qhw-scheduler. If the commit-time `consume()` call returns a
+delayed or rejected decision, QPM treats that decision as final for the qtask
+ID and reports a structured admission commit failure rather than retrying the
+same consumed-attempt key.
 
 Retry should also respond to reservation lifecycle changes. Released,
 cancelled, or expired reservations should cause their pending qtasks to fail or
@@ -1717,9 +1765,11 @@
 reservation capacity. These qtasks have not yet entered qhw-scheduler because
 the admission hold has not been established.
 
-Once capacity is available, QPM retries the hold. If it succeeds, QPM inserts
-the qtask into qhw-scheduler and the scheduler policy controls ordering from
-that point forward.
+Once capacity may be available, QPM retries dry-run authorization for the
+pending entry. If authorization succeeds, QPM calls `consume()` to commit the
+hold. After the accepted consume decision, QPM inserts the qtask into
+qhw-scheduler and the scheduler policy controls ordering from that point
+forward.
 
 </details>
 
```
