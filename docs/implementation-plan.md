# Implementation Plan

The implementation should proceed from infrastructure toward managed execution.
The first phases separate DEFw directory, transport, endpoint resolution, API,
and QPM controller responsibilities. Later phases make `qhw-admission` and
`qhw-scheduler` authoritative for reservation state, usage accounting, task
ordering, and lifecycle state.

## Implementation Discipline

Implementation agents should follow the commit breakdown as closely as
practical. Each commit should stay within the listed phase task, cite the
relevant requirement IDs and detailed-design sections, and avoid combining
unrelated behavior. If implementation evidence shows that a commit must be
split, merged, or reordered, the agent may make that engineering decision as
long as the resulting work still follows `docs/detailed-design.md` and records
the reason in the commit body or implementation notes.

Authentication requirements are tracked separately in
`docs/requirements-authentication.md` and implemented by
`docs/implementation-plan-authentication.md`. This plan keeps token parameters
as unchecked placeholders.

Commit messages must follow the `commit-message-format` skill exactly. Use a
subject of `<sub module>: <short description>`, then a blank line, then a
long-description body with lines capped at 80 characters, and finish with the
exact trailer `Signed-off-by: Amir Shehata <shehataa@ornl.gov>`.

Each implementation commit should cite the suggested commit ID, plan task IDs,
requirement IDs, and detailed-design sections it completes, for example:

```text
Commit: PH1-C06
Plan: PH1.6
Reqs: DISC-001 CTRL-004
Design: Peer Lifecycle Events; DEFw Directory And Identity Model
```

If one commit completes only part of a task, use the same requirement IDs and
design references, then describe the completed slice in the commit body. If
one commit completes multiple tasks, cite the union of the affected task,
requirement, and design references.

## Validation Of Current Plan

The existing plan is directionally correct. It starts with directory and
identity work, keeps reservation ownership out of the DEFw directory service,
introduces a shared QPM controller, integrates admission before scheduler
dispatch, and finishes with telemetry and hardening.

The following adjustments are needed for requirement-level traceability:

1. Split long-running QPM endpoint resolution into an explicit infrastructure
   phase so QFw-managed mode and long-running mode can be validated separately.
2. Add API-category and token-placeholder work before resource-affecting
   execution is migrated to reservation-scoped APIs.
3. Separate admission control workflows from admission policy configuration.
4. Add Qiskit adapter pass-through for `reservation_id`, opaque token, and run
   context so application submissions can reach the target QPM API shape.
5. Specify the active-state reservation close protocol for release, cancel, and
   expiration before terminal qhw-admission lifecycle calls.
6. Specify the `authorize_usage()` and `consume()` boundary so pending-capacity
   work cannot enter qhw-scheduler before an estimated capacity hold exists.
7. Add task lifecycle, structured status, telemetry access partitioning, and
   reconciliation tasks with explicit requirement IDs.
8. Move the DEFw build-system migration to the start of Phase 1 so CMake is
   the baseline before transport and C/Python refactors begin.
9. Replace the legacy DEFw discovery manager with DEFw-dirsvc early in Phase 1
   so directory-service semantics, APIs, configuration, and code names are
   established before the deeper transport and registration refactors.
10. Support multiple directory-service scopes so allocation-local and
   site-scoped long-running services use the same resolver contract.
11. Split the DEFw C transport refactor from the Python directory migration so
   the C-only changes can land as clean commits while preserving the current
   SWIG-exported Python compatibility surface.
12. Treat RPC callbacks and peer lifecycle callbacks as separate contracts.
   Request, response, and event callbacks remain message-plane callbacks,
   while protocol-neutral peer lifecycle events become the transport-owned
   liveness input to the Python directory.

## Detailed Design Traceability

Use this table before implementing a phase. Body sections in
`docs/detailed-design.md` define the architecture and flow. Requirement design
notes provide acceptance details and edge-case behavior. Commit messages should
cite the most specific design section names that governed the change.

| Phase | Primary detailed-design body sections | Requirement design notes |
| --- | --- | --- |
| Phase 1. Directory And Transport Foundation | [Build And Installation Model](detailed-design.md#build-and-installation-model); [DEFw Directory And Identity Model](detailed-design.md#defw-directory-and-identity-model); [Connection Establishment Flow](detailed-design.md#connection-establishment-flow); [Peer Lifecycle Events](detailed-design.md#peer-lifecycle-events); [Heartbeat Policy](detailed-design.md#heartbeat-policy); [Service Registration Flow](detailed-design.md#service-registration-flow); [DEFw Registration Infrastructure Changes](detailed-design.md#defw-registration-infrastructure-changes); [Heartbeat And Liveness Flow](detailed-design.md#heartbeat-and-liveness-flow); [Service Deregistration Flow](detailed-design.md#service-deregistration-flow); [Directory Service Scope And Resolver Policy](detailed-design.md#directory-service-scope-and-resolver-policy) | [OPM-001](detailed-design.md#opm-001); [OPM-003](detailed-design.md#opm-003); [DISC-001](detailed-design.md#disc-001); [DISC-002](detailed-design.md#disc-002); [DISC-004](detailed-design.md#disc-004); [CAT-005](detailed-design.md#cat-005); [CTRL-004](detailed-design.md#ctrl-004) |
| Phase 2. QPM Endpoint Resolution And Operation Modes | [DEFw Directory And Identity Model](detailed-design.md#defw-directory-and-identity-model); [Connection Establishment Flow](detailed-design.md#connection-establishment-flow); [DEFw Registration Infrastructure Changes](detailed-design.md#defw-registration-infrastructure-changes); [Directory Service Scope And Resolver Policy](detailed-design.md#directory-service-scope-and-resolver-policy); [QPM Override Handling](detailed-design.md#qpm-override-handling); [Integration Sequence](detailed-design.md#integration-sequence) | [OPM-001](detailed-design.md#opm-001); [OPM-002](detailed-design.md#opm-002); [OPM-003](detailed-design.md#opm-003); [DISC-003](detailed-design.md#disc-003); [DISC-004](detailed-design.md#disc-004); [DISC-005](detailed-design.md#disc-005); [API-003](detailed-design.md#api-003) |
| Phase 3. API Categories, Token Placeholders, And Client Pass-Through | [Managed Resource Model](detailed-design.md#managed-resource-model); [QFw API Categories](detailed-design.md#qfw-api-categories); [Token Placeholder For Current Milestone](detailed-design.md#token-placeholder-for-current-milestone); [Admission Policy Configuration APIs](detailed-design.md#admission-policy-configuration-apis); [Scheduler Control APIs](detailed-design.md#scheduler-control-apis); [Admission Control APIs](detailed-design.md#admission-control-apis); [Execution APIs](detailed-design.md#execution-apis); [Synchronous Execution Contract](detailed-design.md#synchronous-execution-contract); [Telemetry And Discovery APIs](detailed-design.md#telemetry-and-discovery-apis); [Identifier Allocation And Mapping](detailed-design.md#identifier-allocation-and-mapping) | [CAT-001](detailed-design.md#cat-001) through [CAT-007](detailed-design.md#cat-007); [API-001](detailed-design.md#api-001) through [API-004](detailed-design.md#api-004) |
| Phase 4. QPM Controller Runtime Scaffolding | [Admission And Scheduler Integration](detailed-design.md#admission-and-scheduler-integration); [QPM Override Handling](detailed-design.md#qpm-override-handling); [Integration Sequence](detailed-design.md#integration-sequence); [Identifier Allocation And Mapping](detailed-design.md#identifier-allocation-and-mapping) | [STATE-001](detailed-design.md#state-001) through [STATE-004](detailed-design.md#state-004); [SCHED-002](detailed-design.md#sched-002); [SCHED-003](detailed-design.md#sched-003); [SCHED-009](detailed-design.md#sched-009); [SCHED-010](detailed-design.md#sched-010); [CAT-007](detailed-design.md#cat-007) |
| Phase 5. Admission Control And Reservation Store Integration | [Admission And Scheduler Integration](detailed-design.md#admission-and-scheduler-integration); [Token Placeholder For Current Milestone](detailed-design.md#token-placeholder-for-current-milestone); [Admission Policy Configuration APIs](detailed-design.md#admission-policy-configuration-apis); [Admission Control APIs](detailed-design.md#admission-control-apis); [Integration Sequence](detailed-design.md#integration-sequence); [Identifier Allocation And Mapping](detailed-design.md#identifier-allocation-and-mapping) | [ADM-001](detailed-design.md#adm-001) through [ADM-007](detailed-design.md#adm-007); [ADM-016](detailed-design.md#adm-016) through [ADM-022](detailed-design.md#adm-022); [CAT-003](detailed-design.md#cat-003); [CTRL-005](detailed-design.md#ctrl-005); [CTRL-006](detailed-design.md#ctrl-006); [API-004](detailed-design.md#api-004) |
| Phase 6. Scheduler Integration And Managed Execution | [Admission And Scheduler Integration](detailed-design.md#admission-and-scheduler-integration); [Scheduler Control APIs](detailed-design.md#scheduler-control-apis); [Execution APIs](detailed-design.md#execution-apis); [Synchronous Execution Contract](detailed-design.md#synchronous-execution-contract); [Integration Sequence](detailed-design.md#integration-sequence); [Identifier Allocation And Mapping](detailed-design.md#identifier-allocation-and-mapping) | [SCHED-001](detailed-design.md#sched-001) through [SCHED-014](detailed-design.md#sched-014); [CAT-002](detailed-design.md#cat-002); [CAT-004](detailed-design.md#cat-004); [API-001](detailed-design.md#api-001); [API-004](detailed-design.md#api-004) |
| Phase 7. Telemetry, Reconciliation, And Hardening | [Telemetry And Discovery APIs](detailed-design.md#telemetry-and-discovery-apis); [Admission And Scheduler Integration](detailed-design.md#admission-and-scheduler-integration); [Peer Lifecycle Events](detailed-design.md#peer-lifecycle-events); [Heartbeat Policy](detailed-design.md#heartbeat-policy); [Heartbeat And Liveness Flow](detailed-design.md#heartbeat-and-liveness-flow); [Service Deregistration Flow](detailed-design.md#service-deregistration-flow); [Integration Sequence](detailed-design.md#integration-sequence) | [CAT-005](detailed-design.md#cat-005); [API-002](detailed-design.md#api-002); [API-004](detailed-design.md#api-004); [CTRL-002](detailed-design.md#ctrl-002) through [CTRL-008](detailed-design.md#ctrl-008); [STATE-004](detailed-design.md#state-004); [SCHED-013](detailed-design.md#sched-013); [SCHED-014](detailed-design.md#sched-014) |

## Commit Breakdown

The commit breakdown is the preferred review sequence. A commit may be split
when the implementation is large, but it should not combine unrelated rows.
The commit body should cite the union of the `Reqs:` values from the referenced
plan tasks and the detailed-design sections from the traceability table that
govern the change. Any commit that intentionally has no requirement mapping
must say why, such as build infrastructure cleanup.

### Phase 1 Commit Sequence

Phase 1 is primarily DEFw repository work. Start with the behavior-neutral
CMake migration, then keep the early transport commits C-only where possible
so the SWIG and Python behavior can be validated before the Python agent layer
changes.

| Commit ID | Primary repo | Plan tasks | Scope and exit criteria |
| --- | --- | --- | --- |
| PH1-C01 | DEFw | PH1.1 | Replace SCons with CMake as the authoritative DEFw build and install system while preserving current behavior, the DEFw executor runtime model, SWIG artifacts, extension module names, typemap compatibility, install layout, and test entry points. |
| PH1-C02 | DEFw | PH1.2 | Replace the legacy DEFw discovery manager with DEFw-dirsvc across service names, APIs, configuration, code ownership, tests, and documentation while keeping any required compatibility aliases thin and explicitly deprecated. |
| PH1-C03 | DEFw | PH1.3 | Replace the internal C multi-list model with one authoritative connection table. Preserve current behavior through C-only tests and manual smoke coverage. |
| PH1-C04 | DEFw | PH1.4 | Rebuild the existing SWIG-exported iterator and connect functions as filtered views over the single C table. Python code should not change in this commit. |
| PH1-C05 | DEFw | PH1.5 | Preserve request, response, event, and active-connect callback semantics while the C storage model changes. RPC request/response tests should still pass. |
| PH1-C06 | DEFw | PH1.6 | Add the protocol-neutral C-to-Python peer lifecycle event path, event payload contract, Python dispatch hook, and SWIG payload support. |
| PH1-C07 | DEFw | PH1.7 | Implement explicit heartbeat policy for all non-self live control channels, handle loopback records separately, and collapse transport failures into peer lifecycle outcomes. |
| PH1-C08 | DEFw | PH1.8 | Add the Python peer or agent table, consume only C peer lifecycle updates and connect results, and update infrastructure call sites that only need peer lookup. |
| PH1-C09 | DEFw | PH1.9, PH1.10 | Add logical `service_id`, `runtime_id`, opaque peer binding, generation, and Python directory records without changing discovery semantics yet. |
| PH1-C10 | DEFw | PH1.11, PH1.12 | Implement explicit service-record registration, multiple client/server API binding records, selector metadata, and deregistration against the Python directory and active transport binding. |
| PH1-C11 | DEFw | PH1.13, PH1.14 | Move discovery to registered service records and selected API bindings. Add binding-aware client proxy construction, explicit BaseRemote RPC target overrides, directory lifecycle transitions, inactive retention, stale peer-event rejection, and purge behavior. |
| PH1-C12 | DEFw, QFw | PH1.15 | Remove QPM capacity accounting from DEFw-dirsvc endpoint selection and split endpoint resolution from reservation. |
| PH1-C13 | DEFw | PH1.16 | Add transport, SWIG, callback, directory, and discovery tests that cover the complete Phase 1 migration. |

### Phase 2 Commit Sequence

| Commit ID | Primary repo | Plan tasks | Scope and exit criteria |
| --- | --- | --- | --- |
| PH2-C01 | QFw, DEFw | PH2.1, PH2.2 | Add the QPM resolver abstraction and QFw-managed resolver backend that binds through one or more DEFw-dirsvc endpoints without directory-service capacity semantics. |
| PH2-C02 | DEFw, QFw | PH2.3 | Add long-running service startup/configuration behavior where long-running QPMs register with site-scoped DEFw-dirsvc instances rather than allocation-local launch manifests. |
| PH2-C03 | QFw | PH2.4, PH2.5 | Add multi-directory resolver policy, site-injected directory endpoints, scope annotation, deterministic selection, ambiguity handling, and generation-aware binding behavior. |
| PH2-C04 | QFw, DEFw | PH2.6 | Add operation-mode tests for allocation-local, site-scoped, and combined directory-service resolution. |

### Phase 3 Commit Sequence

| Commit ID | Primary repo | Plan tasks | Scope and exit criteria |
| --- | --- | --- | --- |
| PH3-C01 | QFw | PH3.1 | Split QPM remote APIs into category surfaces and keep compatibility wrappers where needed. |
| PH3-C02 | QFw | PH3.2, PH3.3 | Add request parsing, opaque token placeholders, and structured status envelope fields without token checking. |
| PH3-C03 | QFw | PH3.4 | Keep API category routing separate from caller validation. |
| PH3-C04 | QFw | PH3.5 | Migrate Qiskit backend, job, sampler, and estimator pass-through for reservation ID, token placeholder, and execution context. |
| PH3-C05 | QFw | PH3.6, PH3.7 | Add metadata access placeholders and API/token-pass-through tests. Authentication-provider work moves to `docs/implementation-plan-authentication.md`. |

### Phase 4 Commit Sequence

| Commit ID | Primary repo | Plan tasks | Scope and exit criteria |
| --- | --- | --- | --- |
| PH4-C01 | QFw | PH4.1 | Add the target-scoped QPM controller shell and per-target construction path. |
| PH4-C02 | QFw | PH4.2, PH4.3 | Add runtime correlation maps, qtask ID allocation, and external identifier canonicalization. |
| PH4-C03 | QFw | PH4.4 | Keep the existing provider-QPM inheritance from `UTIL_QPM`, add explicit provider hooks, and move provider-specific public run overrides into shared utility-hook usage. |
| PH4-C04 | QFw | PH4.5 | Add explicit diagnostic bypass controls, audit hooks, and disabled-auth behavior. |
| PH4-C05 | QFw | PH4.6 | Add controller scaffolding tests for target isolation, mapping, hooks, and cleanup. |

### Phase 5 Commit Sequence

| Commit ID | Primary repo | Plan tasks | Scope and exit criteria |
| --- | --- | --- | --- |
| PH5-C01 | QFw | PH5.1, PH5.2 | Construct qhw-admission contexts per target and configure device profiles, capacity models, estimators, and admission policies. |
| PH5-C02 | QFw | PH5.3 | Implement admission workflows backed by qhw-admission without a duplicate QPM reservation database. |
| PH5-C03 | QFw | PH5.4 | Enforce reservation validation and caller binding before resource-affecting work. |
| PH5-C04 | QFw | PH5.5, PH5.6 | Add estimated usage authorization, committed holds, pending-capacity policy, and retry behavior. |
| PH5-C05 | QFw | PH5.7 | Implement active-state reservation close ordering for release, cancel, and expiration. |
| PH5-C06 | QFw | PH5.8 | Add admission integration tests, including concurrency and accounting reconciliation cases. |

### Phase 6 Commit Sequence

| Commit ID | Primary repo | Plan tasks | Scope and exit criteria |
| --- | --- | --- | --- |
| PH6-C01 | QFw | PH6.1, PH6.2 | Construct qhw-scheduler contexts per target and add scheduler control APIs. |
| PH6-C02 | QFw | PH6.3, PH6.4 | Insert only admitted qtasks into qhw-scheduler and dispatch only scheduler-selected work to QRC/provider paths. |
| PH6-C03 | QFw | PH6.5 | Migrate `sync_run()` and `async_run()` to the shared reservation-scoped managed path. |
| PH6-C04 | QFw | PH6.6 | Enforce scheduler and admission completion ordering before terminal results become visible. |
| PH6-C05 | QFw | PH6.7, PH6.8 | Implement cancellation propagation, managed task status, queue positions, and wait estimates. |
| PH6-C06 | QFw | PH6.9 | Add scheduler integration tests for dispatch, timeout, cancellation, status, and failure behavior. |

### Phase 7 Commit Sequence

| Commit ID | Primary repo | Plan tasks | Scope and exit criteria |
| --- | --- | --- | --- |
| PH7-C01 | QFw | PH7.1 | Implement telemetry/discovery access partitioning. |
| PH7-C02 | QFw | PH7.2, PH7.3 | Add admission capacity snapshots, queue metrics, and estimate telemetry. |
| PH7-C03 | QFw | PH7.4, PH7.5 | Add reconciliation, recovery, service lifecycle telemetry, and audit records. |
| PH7-C04 | DEFw, QFw | PH7.6 | Remove DEFw four-list compatibility exports, Python view adapters, deprecated directory reservation compatibility, and unmanaged execution bypasses. |
| PH7-C05 | QFw, DEFw | PH7.7 | Add end-to-end and operations tests across operation modes, managed execution, telemetry, reconciliation, SWIG behavior, and build outputs. |

## Phase 1. Directory And Transport Foundation

Phase 1 replaces the legacy DEFw discovery manager with DEFw-dirsvc and makes
the service a service-record, API-binding, and endpoint directory instead of a
QPM capacity owner. This phase is scoped to the DEFw repository and starts by
replacing SCons with CMake. The rename and the C transport-layer and C/Python
interaction refactors follow after the CMake build reproduces the current DEFw
outputs. Provider execution behavior should remain unchanged in this phase.

Primary requirements: `OPM-001`, `OPM-003`, `DISC-001`, `DISC-002`,
`DISC-004`, `CAT-005`, `CTRL-004`.

1. PH1.1 Replace SCons with CMake for DEFw.
   - Implement the
     [Build And Installation Model](detailed-design.md#build-and-installation-model)
     from the detailed design.
   - Audit the current SCons build for DEFw C sources, SWIG interface
     generation, Python extension outputs, install layout, test integration,
     generated files, and developer workflows.
   - Add CMake as the authoritative DEFw build system for C sources, SWIG
     interface generation, Python extension modules, install layout, test
     integration, generated files, and developer workflows.
   - Build generated SWIG wrappers in the CMake build tree. Do not generate
     `.i`, wrapper C, generated Python proxy, or staging files into the source
     tree.
   - Add an explicit installation system for DEFw headers, the C library,
     generated Python extension module, Python package files, SWIG typemap
     includes, tests or test entry points, and CMake package metadata.
   - Export a CMake package that supports `find_package(DEFw CONFIG REQUIRED)`
     and a stable `DEFw::defw` imported target for C/C++ consumers.
   - Preserve the current DEFw executor model. QFw applications should keep
     running inside the DEFw-provided Python execution environment, with DEFw
     glue code and SWIG bindings already available through that environment.
   - Treat the installed DEFw executor as a supported runtime interface.
     Define stable launcher entry points, path and bootstrap behavior,
     shared-library lookup, version and runtime assumptions, and install-tree
     tests as part of the CMake migration.
   - Install DEFw executor or launcher entry points, runtime bootstrap files,
     Python package files, and path configuration so the execution environment
     works from an install prefix rather than from the DEFw source tree.
   - Do not require QFw application code to add an explicit `import defw` to
     obtain the DEFw execution environment. Direct `import defw` may remain
     available for tests, tools, downstream wrappers, or interactive debugging,
     but it is not the primary QFw integration model.
   - Preserve runtime search path behavior so installed Python extensions can
     locate the installed DEFw shared library.
   - Preserve SWIG-generated Python bindings, C/Python include paths,
     extension-module naming, runtime search paths, packaging outputs, and
     test entry points.
   - Split SWIG typemaps into explicit include files for current compatibility
     behavior, owned string output, counted owned string-list output, and typed
     opaque handles.
   - Preserve current DEFw Python wrapper return shapes unless an individual
     wrapper is explicitly migrated with tests.
   - Keep compatibility typemaps for existing `char **` and `char ***`
     wrappers while adding target typemaps for new/refactored APIs.
   - Define the `char **` owned-output contract as malloc/calloc-transferred
     memory. A NULL output means allocation failure and must raise a Python
     memory/allocation exception rather than returning `None`.
   - Add a counted `char ***out, size_t *count` string-list typemap that
     returns `list[str]` and frees each transferred string and the transferred
     array.
   - Keep existing uncounted `char ***` wrapper behavior until each current
     API is audited and migrated deliberately.
   - Remove the dead commented global `void *` typemap. Use typed opaque
     handles or library-specific SWIG modules instead.
   - Keep external-library wrapping possible by making generic typemaps
     opt-in includes. Do not force every SWIG module to inherit DEFw-specific
     pointer semantics.
   - Remove SCons once CMake produces equivalent artifacts and existing
     developer/test workflows have CMake entry points.
   - Keep the build-system migration behavior-neutral so the transport and
     C/Python refactors start from a stable CMake baseline.
   - Add build-tree and install-tree validation for CMake build output, CMake
     package discovery, installed DEFw executor launch, QFw execution inside
     the DEFw Python environment without explicit application-level
     `import defw`, optional Python package import, SWIG output compatibility,
     typemap behavior, and runtime search paths.
   - Reqs: none; build infrastructure cleanup.

2. PH1.2 Replace the legacy discovery manager with DEFw-dirsvc.
   - Rename the DEFw discovery service, modules, scripts, configuration keys,
     log labels, service names, and tests to use
     `DEFw-dirsvc` or `dirsvc` where the component acts as the directory
     service.
   - Rename public and internal directory APIs so registration, deregistration,
     service lookup, endpoint resolution, and API-binding lookup no longer use
     resource reservation terminology.
   - Use configuration names such as `register-with-dirsvc` and
     `dirsvc-endpoint` for directory-service registration.
   - Keep any required compatibility aliases thin, local to this transition,
     and explicitly deprecated. New implementation work must call the
     `dirsvc` names.
   - Keep QPM reservation, admission, scheduler, and capacity ownership out of
     DEFw-dirsvc. The directory service only registers services, resolves
     endpoints and API bindings, and tracks service lifecycle.
   - Update QFw integration references and tests that launch or discover the
     directory service.
   - Reqs: `DISC-001`, `DISC-002`, `DISC-004`.

3. PH1.3 Refactor C to one authoritative connection table.
   - Replace the authoritative C `agent_new_list`, `agent_service_list`,
     `agent_client_list`, `agent_active_service_list`,
     `agent_active_client_list`, and `agent_dead_list` model with one
     C-owned connection table or list.
   - Store connection direction as data, such as `INBOUND` for accepted
     passive connections and `OUTBOUND` for locally initiated active
     connections.
   - Store peer role as data, such as DEFw agent, DEFw service, and
     DEFw-dirsvc.
   - Store connection lifecycle state, control FD, RPC FD, remote runtime UUID,
     connection-block UUID, endpoint metadata, listen endpoint metadata,
     heartbeat timestamp, failure reason, and reference count state in each
     connection record.
   - Add fields for local loopback detection, heartbeat mode, last heartbeat
     transmit time, last heartbeat receive time, last control-channel activity
     time, and handshake deadline.
   - Keep inbound control/RPC socket consolidation correct by matching on
     direction, remote runtime UUID, and channel state instead of relying on
     separate service and client lists.
   - Keep outbound connect setup as one record that owns both the control and
     RPC sockets it initiates.
   - Reqs: `DISC-001`, `DISC-002`.

4. PH1.4 Preserve SWIG and Python compatibility during the C-only refactor.
   - Keep the existing SWIG-exported C functions used by Python, including
     `defw_get_next_service_agent()`, `defw_get_next_client_agent()`,
     `defw_get_next_active_service_agent()`, and
     `defw_get_next_active_client_agent()`.
   - Reimplement those exports as filtered views over the single C connection
     table, using direction and peer role filters.
   - Preserve existing pointer ownership, reference acquisition, release
     semantics, string allocation behavior, and Python-visible field names so
     the current `DEFwAgents` wrappers continue to work unchanged.
   - Keep `defw_connect_to_service()` and `defw_connect_to_client()` as stable
     SWIG entry points while moving their internals to the single-table
     connection model.
   - Reqs: `DISC-001`, `DISC-002`.

5. PH1.5 Clarify and preserve the current RPC callbacks.
   - Keep `EN_PY_CB_REQUEST`, `EN_PY_CB_RESPONSE`, and `EN_PY_CB_EVENT` as
     message-plane callbacks for DEFw RPC request, response, and event
     payload delivery.
   - Keep `EN_PY_CB_CONNECT` as active connect request completion. It should
     continue to satisfy Python `WR_CONNECT` waiters and should not become the
     general lifecycle notification path.
   - Preserve the current Python callback signatures during the C-only
     refactor so request/response routing, blocking RPC waits, and active
     connection waits do not change in the same commit as the C table
     refactor.
   - Reqs: `DISC-001`.

6. PH1.6 Add a protocol-neutral C-to-Python peer lifecycle path.
   - Add a separate peer lifecycle callback or event API instead of
     overloading `EN_PY_CB_CONNECT`.
   - Emit Python-visible events only for `PEER_READY`, optional
     `PEER_DEGRADED`, `PEER_LOST`, and `PEER_REMOVED`.
   - Keep accepted sockets, outbound connect starts, control-channel setup,
     RPC-channel setup, heartbeat success, socket close, connection death, and
     purge as C transport details.
   - Include opaque peer handle, remote runtime UUID when known, loopback
     classification, protocol-neutral transport context, endpoint metadata,
     reason, and timestamp in the event payload.
   - Do not expose channel, FD, socket, connection-list, heartbeat-mode, or
     libfabric-specific endpoint fields to Python.
   - Define the Python dispatch contract for the new event path, with C peer
     events entering Python through a worker-safe queue such as
     `defw_workers.put_peer_event(event)`.
   - Keep registration and deregistration APIs as the only operations that
     create or remove logical directory registration identity.
   - Preserve SWIG typemaps and generated wrapper behavior for existing
     callbacks while adding the peer event payload path.
   - Reqs: `DISC-001`, `CTRL-004`.

7. PH1.7 Fix C heartbeat and liveness coverage.
   - Implement the [Heartbeat Policy](detailed-design.md#heartbeat-policy)
     from the detailed design.
   - Select heartbeat behavior per C connection record instead of inheriting it
     from service, client, active-service, or active-client list membership.
   - Mark records whose remote runtime identity matches the local runtime
     identity as `SELF` or local loopback records.
   - Use `heartbeat_mode = NONE` for loopback records. They must not enter the
     remote heartbeat send path or remote heartbeat timeout path.
   - Apply heartbeat send, receive, failure, timeout, and close handling to all
     non-self live control channels, independent of inbound or outbound
     direction and independent of agent, service, or dirsvc peer role.
   - Use a handshake timeout for accepted sockets that have not completed
     session identity exchange. Do not treat unidentified sockets as heartbeat
     timeouts.
   - Keep heartbeat success internal to C unless a telemetry API requests
     aggregated transport health.
   - Collapse heartbeat failure, heartbeat timeout, socket failure, socket
     close, connection death, and cleanup into the smallest Python-visible
     peer lifecycle event needed for directory behavior.
   - Keep socket cleanup and reference-count cleanup independent from Python
     directory state transitions.
   - Reqs: `DISC-001`, `CTRL-004`.

8. PH1.8 Migrate the Python DEFw agent layer to an opaque peer table.
   - Add one Python peer or agent table that is updated only by C peer
     lifecycle events and C-produced outbound connect results.
   - Do not add Python polling, socket inspection, heartbeat refresh, or C
     connection-list reloads as normal liveness mechanisms.
   - Allow a C-provided peer snapshot only for startup recovery and tests. The
     snapshot is a resynchronization from the C source of truth.
   - Add an `apply_peer_event()` path that handles peer lifecycle events
     idempotently by peer handle, runtime identity, and event timestamp.
   - Store opaque peer handle, runtime ID, endpoint metadata, transport
     context, callability state, `last_seen`, and loss reason in the peer
     table.
   - Keep accepted sockets, outbound connect starts, channel identity, and
     heartbeat state out of Python records. Only ready peers enter the table.
   - Treat `PEER_LOST` as a directory transition input only for already
     registered services or clients. Treat `PEER_REMOVED` as peer table
     cleanup, not as a service lifecycle state.
   - Treat loopback peer records as local callability state. They should not
     create remote-liveness directory transitions.
   - Derive the old `client_agents`, `service_agents`,
     `active_client_agents`, and `active_service_agents` views as compatibility
     filters over peer or directory state only where existing higher-level code
     still needs those names.
   - Update `get_agent()`, `connect_to_services()`, dirsvc lookup, worker
     refresh handling, and debugging dump helpers to use the peer table.
   - Reqs: `DISC-001`, `DISC-002`.

9. PH1.9 Introduce logical registration identity.
   - Add stable `service_id` fields to service and client registration
     payloads.
   - Add a generic `service_type` field for service-family selection.
   - Keep the existing DEFw process UUID as `runtime_id`.
   - Expose an opaque `peer_handle` for Python registration binding. Keep C
     connection UUIDs and block UUIDs inside the transport layer.
   - Add a directory-owned `generation` that increments when a known
     `service_id` registers with a new runtime.
   - Reqs: `DISC-001`, `OPM-001`.

10. PH1.10 Move directory authority to Python.
   - Key service and client records by `service_id`.
   - Store registration kind, endpoint, runtime ID, opaque peer handle,
     service type, API bindings, selector metadata, lifecycle state,
     `last_seen`, and `generation` in the Python directory.
   - Keep C connection UUIDs and block UUIDs internal to the transport layer.
     The directory references only the opaque peer binding reported by C.
   - Reqs: `DISC-001`, `DISC-002`.

11. PH1.11 Implement explicit service registration.
   - Add `register_service()` metadata for the service record defined in the
     detailed design, including `service_id`, `service_type`, `runtime_id`,
     generation, endpoint, API bindings, and selector metadata.
   - Attach the active opaque peer handle from the RPC peer context. The
     service registration payload should not provide socket, channel, block
     UUID, or connection-list details.
   - Support multiple API bindings for one logical service. Each binding
     should carry `binding_name`, `client_module`, `client_class`,
     `service_module`, `service_class`, and binding version.
   - Allow multiple client API classes to route to the same service-side class
     when that class implements the selected surfaces. Also allow separate
     service-side adapter classes when a service family chooses that layout.
   - Treat selector metadata as shallow discovery data. `selector.resources`
     should name logical targets exposed for selection, not internal routing
     libraries or provider SDKs.
   - Validate the registration against the active ready peer before the record
     enters normal discovery.
   - Create generation 1 for a new `service_id`.
   - Reuse inactive records by incrementing `generation`, replacing the active
     runtime binding, and marking the record `UP`.
   - Reject concurrent live registrations for the same `service_id` unless an
     explicit takeover policy is configured.
   - Reqs: `DISC-001`, `DISC-004`, `OPM-001`.

12. PH1.12 Implement explicit service deregistration.
   - Add `deregister_service(service_id, runtime_id, generation)`.
   - Validate the active generation and runtime binding before removing the
     endpoint.
   - Mark the directory record `DEREGISTERED` and start inactive-record
     retention.
   - Reqs: `DISC-001`.

13. PH1.13 Replace query-on-discovery behavior.
   - Make registration provide the service record used by discovery.
   - Make `resolve_services()` read the Python directory instead of rebuilding the
     directory by reloading C lists and querying every service.
   - Add filters for service name, service type, API binding name, client API
     class, service API class, selector name, selector aliases, and selector
     resources.
   - Return the selected service record and selected API binding together so a
     client can construct the correct `BaseRemote` proxy.
   - Replace the implicit `service_apis[res_name].res_name` construction
     pattern with a binding-aware helper that imports the selected
     `client_module.client_class`.
   - Extend `BaseRemote` with optional remote module and remote class
     overrides from the selected binding. Use those overrides for
     `instantiate_class`, `method_call`, and `destroy_class` RPCs.
   - Preserve the existing class-name convention as the compatibility fallback
     when no explicit binding target override is supplied.
   - Reqs: `DISC-001`, `CAT-005`.

14. PH1.14 Implement the directory lifecycle state machine.
   - Define `UP`, `DOWN`, `TIMED_OUT`, and `DEREGISTERED` in the Python
     directory layer.
   - Route registration, C peer lifecycle loss, and explicit deregistration
     through one transition function.
   - Consume peer lifecycle events only as inputs to lifecycle transitions for
     already registered services. Peer readiness alone must not create
     discoverable directory records.
   - Store `last_seen`, `state_changed_at`, `down_reason`,
     `retention_deadline`, and `generation`.
   - Normal discovery returns only live endpoints. Operator directory queries
     may include inactive records with lifecycle metadata.
   - Delete inactive records when retention expires. Purge is a deletion
     action, not a service state.
   - Ignore stale peer events that reference an older `runtime_id`,
     peer handle, or generation after a newer runtime is active.
   - Reqs: `DISC-001`, `CTRL-004`.

15. PH1.15 Remove QPM capacity semantics from DEFw-dirsvc.
   - Split endpoint resolution from QPM reservation.
   - Add a directory resolve operation that returns service records,
     endpoints, and selected API bindings, including `service_id`,
     `service_type`, `runtime_id`, `generation`, endpoint, and selector
     metadata.
   - Add a client binding path that connects to a selected endpoint without
     calling any directory `reserve()` path.
   - Route the new client binding path through the selected API binding so QFw
     can request execution, telemetry, admission, or control surfaces without
     forcing the client class and service class to share a name.
   - Remove `DEFwServiceInfo.consume_capacity()` from the QPM connection path.
   - Remove directory `reserve()` and `release()` semantics for QPM.
   - Reqs: `DISC-002`, `DISC-004`, `OPM-003`.

16. PH1.16 Add DEFw transport, SWIG, and directory tests.
   - Cover the single C connection table, compatibility filtered iterators,
     SWIG wrapper behavior, active and passive connection setup, control and
     RPC channel pairing, request and response callbacks, active connect
     callback completion, protocol-neutral peer lifecycle events, loopback
     heartbeat policy, handshake timeout, heartbeat-driven peer loss,
     transport failure peer loss, registration with multiple API bindings,
     binding-aware discovery, deregistration, restart with the same
     `service_id`, stale-event rejection, discovery filtering, operator
     inactive queries, retention purge, and the absence of QPM capacity
     accounting in DEFw-dirsvc.
   - Reqs: `DISC-001`, `DISC-002`, `DISC-004`, `CTRL-004`.

## Phase 2. QPM Endpoint Resolution And Operation Modes

Phase 2 gives clients one QPM resolution path that talks to directory
services. Allocation-local services register with a job-local DEFw-dirsvc.
Long-running services register with one or more site-scoped DEFw-dirsvc
instances whose endpoints are injected into the allocation by trusted site
infrastructure. Direct configured QPM endpoint lookup is retained only as a
controlled fallback or diagnostic path.

Primary requirements: `OPM-001`, `OPM-002`, `OPM-003`, `DISC-003`,
`DISC-004`, `DISC-005`, `API-003`.

1. PH2.1 Add a QPM resolver abstraction.
   - Accept requested service name, service type, API binding name, selector
     resource, selector alias, and QFw API category.
   - Accept a configured set of DEFw-dirsvc endpoints, including
     allocation-local and site-scoped entries.
   - Map QFw API categories to concrete DEFw API binding filters without
     requiring DEFw to understand QFw category semantics.
   - Return one binding shape containing selector metadata, endpoint,
     `service_id`, `service_type`, `runtime_id`, `generation`, directory
     scope, directory identity, and selected API binding.
   - Provide a client-facing `resolver.connect(...)` path that resolves the
     service and selected binding, connects to the endpoint, and returns the
     selected client proxy object.
   - Replace direct QFw use of legacy `defw.connect_to_binding(chosen,
     "QPM")` with the resolver plus binding-aware DEFw connection helper.
   - Reqs: `DISC-004`, `API-003`.

2. PH2.2 Implement the QFw-managed resolver backend.
   - Preserve QFw-managed launch of DEFw-dirsvc and QPM services.
   - Resolve QPM through registered service records and selected API bindings
     in DEFw-dirsvc.
   - Bind to the selected QPM endpoint without using directory-service
     capacity semantics.
   - Treat `qfw_services.yaml` as the allocation launch manifest for services
     started inside the job. It should not be the primary discovery mechanism
     for long-running services.
   - Reqs: `OPM-001`, `OPM-003`, `DISC-001`, `DISC-002`, `API-003`.

3. PH2.3 Implement long-running QPM service startup configuration.
   - Support long-running QPM services that register with a site-scoped
     DEFw-dirsvc managed by site, partition, node group, or service group.
   - Define privileged site configuration for allowed site-scoped
     DEFw-dirsvc endpoints and trust material.
   - Have SLURM, resource-manager, prolog, or equivalent trusted launch code
     inject allocation-scoped DEFw-dirsvc endpoint configuration into the job.
   - Keep `qfw_services.yaml` focused on allocation-launched services. Do not
     require it to list every long-running QPM endpoint.
   - Keep direct QPM endpoint configuration available only for fallback,
     diagnostics, or controlled development.
   - Reqs: `OPM-002`, `DISC-003`, `DISC-005`.

4. PH2.4 Implement multi-directory resolver policy.
   - Query allocation-local and site-scoped DEFw-dirsvc endpoints according to
     configured policy.
   - Annotate returned service records with directory scope and directory
     identity.
   - Filter candidates by service type, selector resource or alias, API
     binding, caller policy, and operation mode.
   - Apply deterministic ordering and tie-breakers when more than one
     candidate matches.
   - Return a structured ambiguity or policy error when multiple candidates
     match and no safe default exists.
   - Do not silently replace a requested hardware service with a simulator.
     Hardware delay or rejection is an admission/scheduler outcome; simulator
     fallback requires explicit workflow, caller, or site policy.
   - Defer load-aware endpoint selection across multiple matching services to
     a later QFw scheduler layer. The baseline resolver should only apply
     deterministic policy or return an explicit ambiguity error.
   - Keep direct configured QPM endpoint resolution as an explicitly enabled
     fallback or debug path, not the primary long-running service model.
   - Reqs: `DISC-004`, `DISC-005`, `API-003`.

5. PH2.5 Enforce generation-aware client binding.
   - Reject stale endpoint generations during binding or before service calls
     when the directory reports a newer active generation.
   - Return the same externally visible reservation, release, and execution
     semantics after binding in both operation modes.
   - Reqs: `DISC-004`, `API-003`.

6. PH2.6 Add operation-mode tests.
   - Cover QFw-managed allocation-local discovery, site-scoped long-running
     discovery, simultaneous allocation-local and site-scoped directories,
     directory ordering, scope annotation, deterministic tie-breaking,
     ambiguity errors, explicit simulator fallback policy, direct endpoint
     fallback/debug behavior, service-record and API binding validation, stale
     generation rejection, and identical QPM API behavior after binding.
   - Reqs: `OPM-001`, `OPM-002`, `OPM-003`, `DISC-003`, `DISC-004`,
     `DISC-005`, `API-003`.

## Phase 3. API Categories, Token Placeholders, And Client Pass-Through

Phase 3 creates the remote API shape and client pass-through needed before
reservation-scoped execution becomes mandatory. The APIs accept `token`
parameters, but authentication is disabled in this milestone. QPM treats token
values as opaque metadata and does not parse, validate, or enforce them.
The full authentication feature is tracked in
`docs/implementation-plan-authentication.md`.

Primary requirements: `CAT-001` through `CAT-007`, `API-001` through
`API-004`.

1. PH3.1 Split the QPM service APIs by category.
   - Define execution, admission control, admission policy configuration,
     scheduler control, and telemetry/discovery API surfaces.
   - Implement each category surface as a concrete DEFw API binding when the
     deployment uses separate API classes, such as execution, admission, and
     telemetry bindings on the same logical service.
   - Keep category routing in QFw. DEFw should only store and route the
     selected module/class binding.
   - Treat binding policy labels as service-family metadata. DEFw may carry
     them as opaque binding fields, but QFw or the service API must interpret
     and enforce them.
   - Keep the QPM service process and shared controller as the implementation
     owner behind those API surfaces.
   - Add compatibility wrappers for existing `api_qpm.QPM` callers where a
     transition period is required.
   - Reqs: `CAT-001`, `CAT-002`, `CAT-003`, `CAT-004`, `CAT-005`,
     `CAT-006`, `CAT-007`.

2. PH3.2 Normalize request parsing.
   - Parse `reservation_id`, opaque `token`, timeout, `cancel_on_timeout`,
     owner metadata, job or allocation ID, project or session ID, target
     device ID, scope ID, workload metadata, and policy metadata.
   - Treat caller-supplied owner, job, allocation, project, session, and token
     values as unverified metadata in this milestone.
   - Do not make task submission idempotency keys part of the required target
     API; each accepted `sync_run()` or `async_run()` creates an independent
     qtask.
   - Reqs: `API-001`, `API-004`, `CAT-002`.

3. PH3.3 Add no-op token plumbing.
   - Preserve token parameters in API method signatures and request records.
   - Store or forward the token only as opaque metadata.
   - Do not parse, validate, normalize, or reject based on token contents.
   - Add an explicit disabled-auth configuration flag or runtime mode so the
     later authentication feature can fail closed when enabled.
   - Reqs: `API-001`, `API-004`, `CAT-002`.

4. PH3.4 Keep category routing separate from token checking.
   - Map selected DEFw API bindings to QFw API surfaces for routing and client
     construction.
   - Keep binding policy labels as opaque metadata in this milestone.
   - Do not enforce binding/category permissions in this milestone.
   - Reqs: `CAT-001`, `CAT-002`, `CAT-003`, `CAT-004`, `CAT-005`.

5. PH3.5 Migrate Qiskit client pass-through.
   - Route QPM lookup through the resolver from Phase 2.
   - Preserve `reservation_id`, opaque token, and execution options from
     `QFwBackend.run()` into `QFwJob`.
   - Forward the same context from `QFwJob` into reservation-scoped QPM
     execution requests.
   - Preserve `QFwSamplerV2` `run_options` and add matching Estimator
     pass-through for reservation context across derived circuits.
   - Reject production execution submissions that lack required reservation
     context.
   - Reqs: `API-001`, `API-003`, `CAT-002`.

6. PH3.6 Preserve metadata API shape.
   - Keep metadata, topology, calibration, and backend information APIs
     separated from resource-affecting execution APIs.
   - Preserve token parameters as placeholders on metadata APIs.
   - Do not enforce policy-controlled filtering in this milestone.
   - Reqs: `API-002`, `API-004`, `CAT-005`.

7. PH3.7 Add API and token-pass-through tests.
   - Cover category routing, compatibility wrappers, opaque token propagation,
     disabled-auth behavior, Qiskit pass-through, reservation context
     propagation, and metadata API shape.
   - Reqs: `CAT-001`, `CAT-002`, `CAT-003`, `CAT-004`, `CAT-005`,
     `API-001`, `API-002`, `API-003`, `API-004`.

## Phase 4. QPM Controller Runtime Scaffolding

Phase 4 creates the shared `UTIL_QPM` controller state and provider hooks while
keeping the actual qhw-admission and qhw-scheduler calls behind interfaces that
can be tested incrementally.

Primary requirements: `STATE-001` through `STATE-004`, `SCHED-002`,
`SCHED-003`, `SCHED-009`, `SCHED-010`, `CAT-007`.

1. PH4.1 Add a target-scoped QPM controller.
   - Construct the controller from `UTIL_QPM.__init__()` after QRC selection.
   - Maintain one controller scope per managed QPU or execution target.
   - Keep per-client DEFw remote proxy objects lightweight. They should hold
     connection and binding context, not reservation, scheduler, provider,
     task, or capacity state.
   - Route selected QPM API bindings to service-side methods or adapter
     objects that delegate to the target-scoped controller.
   - Ensure multiple client bindings for the same logical QPM service do not
     create independent controller state.
   - Record selected library threading mode and controller serialization mode
     in telemetry.
   - Reqs: `SCHED-002`, `STATE-001`, `CAT-007`.

2. PH4.2 Add runtime correlation maps.
   - Track reservation IDs, job IDs, QPM qtask IDs, QFw circuit IDs,
     qhw-scheduler task IDs, qhw-admission usage events, provider job handles,
     token placeholder metadata, request owner metadata, pending-capacity
     entries, capacity holds, worker state, event endpoints, callback
     endpoints, timeout state, and result state.
   - Associate every reservation-scoped runtime object with its reservation ID.
   - Reqs: `STATE-001`, `STATE-002`, `STATE-003`, `SCHED-003`.

3. PH4.3 Define identifier allocation and canonicalization.
   - Allocate a stable QPM qtask ID before admission usage calls or scheduler
     insertion.
   - Keep QFw circuit ID as the client-visible execution handle.
   - Store mappings among `cid`, qtask ID, reservation ID, scheduler task ID,
     and provider handle.
   - Canonicalize site user, job, allocation, project, and session identifiers
     before passing numeric fields to qhw libraries, while preserving original
     values as metadata.
   - Reqs: `STATE-001`, `STATE-002`, `STATE-003`, `ADM-006`.

4. PH4.4 Add provider hooks in the shared utility layer.
   - Keep the existing model where provider QPM classes inherit from
     `UTIL_QPM`.
   - Make `UTIL_QPM.sync_run()` and `UTIL_QPM.async_run()` the shared managed
     entry points for reservation-scoped execution.
   - Provide hooks for circuit preparation, provider submission preparation,
     scheduler-selected submission, completion handling, cancellation, and
     provider shutdown.
   - Convert existing `create_circuit()` provider overrides into
     `prepare_circuit(info)` hook implementations.
   - Convert QB-specific `qb_common_run()` behavior into
     `prepare_provider_submission(circuit)` so vQPU configuration happens
     inside the shared managed path.
   - Remove or reduce provider-specific public `sync_run()` and `async_run()`
     overrides to compatibility wrappers after the hook path is in place.
   - Keep provider calls outside the controller lock and re-enter the
     controller on completion, failure, timeout, or cancellation.
   - Reqs: `SCHED-009`, `SCHED-010`, `STATE-001`, `STATE-004`, `CAT-007`.

5. PH4.5 Add diagnostic bypass controls.
   - Keep any provider-direct or scheduler-bypass execution path visibly
     separate from normal application execution APIs.
   - Require explicit configuration before bypass execution can run.
   - Keep any token or caller parameters as placeholders in this milestone.
   - Emit telemetry or audit records for bypass activity.
   - Reqs: `SCHED-009`, `SCHED-010`, `API-004`.

6. PH4.6 Add controller scaffolding tests.
   - Cover per-target state isolation, runtime mappings, qtask ID stability,
     provider hook dispatch, lock boundaries, cancellation lookup, cleanup on
     terminal state, and diagnostic bypass configuration.
   - Reqs: `STATE-001`, `STATE-002`, `STATE-003`, `STATE-004`,
     `SCHED-002`, `SCHED-003`, `SCHED-009`, `SCHED-010`.

## Phase 5. Admission Control And Reservation Store Integration

Phase 5 connects QPM admission APIs and reservation-scoped admission state to
`qhw-admission`. Work can still use a test scheduler adapter until Phase 6
switches normal execution to `qhw-scheduler`.

Primary requirements: `ADM-001` through `ADM-007`, `ADM-016` through
`ADM-022`, `CAT-003`, `CTRL-005`, `CTRL-006`, `API-004`.

1. PH5.1 Construct admission contexts per target.
   - Create one `qhw_adm_t` context per QPM-managed execution target.
   - Use `QHW_ADM_THREAD_SAFE` by default because DEFw RPC handlers,
     dispatcher threads, timeout handling, and QRC completion callbacks can
     touch target state concurrently.
   - Allow `QHW_ADM_THREAD_USER` only when the controller serializes all calls
     for the target.
   - Reqs: `ADM-001`, `SCHED-002`, `STATE-001`.

2. PH5.2 Implement admission policy configuration.
   - Configure qhw-admission device profiles before accepting reservations.
   - Configure capacity models, estimator policy, admission policy, TTLs,
     scope metadata, and policy options through control-plane APIs.
   - Store policy, estimator, and device-profile versions where audit and
     later interpretation require them.
   - Reqs: `CTRL-005`, `CTRL-006`, `CTRL-001`, `CAT-003`.

3. PH5.3 Implement admission control workflows.
   - Implement `evaluate()`, `reserve()`, `renew()`, `release()`, `cancel()`,
     `get_reservation()`, and `list_reservations()`.
   - Translate QFw reservation requests into qhw-admission request structures.
   - Do not add a durable QPM reservation database that duplicates
     qhw-admission reservation state; query qhw-admission when reservation
     details are needed.
   - Store request-supplied owner, job or allocation metadata, device ID,
     scope, expiration, and policy metadata in the reservation record without
     authenticating those fields in this milestone.
   - Return accepted, delayed, and rejected decisions with machine-readable
     reasons.
   - Reqs: `ADM-001`, `ADM-002`, `ADM-003`, `ADM-004`, `ADM-016`,
     `ADM-017`, `CAT-003`.

4. PH5.4 Enforce reservation validation before resource-affecting work.
   - Query qhw-admission for the reservation record before performing,
     queueing, cancelling, or publishing reservation-scoped work.
   - Require active state, non-expired lifetime, matching device, matching
     scope, and operation compatibility.
   - Keep caller identity fields as request metadata in this milestone.
   - Start the expiration close protocol when a request observes an active but
     expired reservation.
   - Reqs: `ADM-005`, `API-004`.

5. PH5.5 Add estimated usage authorization and holds.
   - Estimate qtask capacity using credits, rate allowance, shot count, circuit
     count, walltime, estimated device time, or policy-specific capacity.
   - Use QPM qtask ID as `qhw_adm_usage_t.task_id`.
   - Call `authorize_usage()` as a dry-run capacity probe.
   - Call `consume()` only after accepted authorization and only when the qtask
     is ready to enter scheduler insertion.
   - Treat accepted `consume()` as the committed in-flight hold.
   - Reqs: `ADM-006`, `ADM-018`, `ADM-019`, `STATE-003`.

6. PH5.6 Implement pending-capacity policy.
   - For delayed authorization, either reject, return delayed status, or place
     the qtask in a QPM-managed pending queue according to site policy.
   - Pending qtasks must not have qhw-admission usage events and must not enter
     qhw-scheduler.
   - Retry pending work with `authorize_usage()` using the same qtask ID and
     identical usage payload.
   - If commit-time `consume()` returns delayed or rejected, report an
     admission commit failure instead of retrying the same consumed-attempt key.
   - Reqs: `ADM-019`, `ADM-020`, `ADM-022`, `SCHED-004`.

7. PH5.7 Implement active-state reservation close protocol.
   - On release, cancel, or expiration, mark the reservation closing in QPM
     runtime state and reject new resource-affecting work.
   - Remove pending-capacity entries that never obtained a hold.
   - Drain, cancel, fail, or reconcile held qtasks according to site policy.
   - Call `return_usage()` for unused estimated capacity and `record_actual()`
     for known measured usage while qhw-admission still reports the reservation
     as active.
   - Call `qhw_adm_release()`, `qhw_adm_cancel()`, or `qhw_adm_expire()` only
     after held-task accounting is complete or after QPM emits a reconciliation
     fault.
   - Reqs: `ADM-003`, `ADM-007`, `ADM-017`, `ADM-021`, `ADM-022`,
     `STATE-004`.

8. PH5.8 Add admission integration tests.
   - Cover reservation creation with unverified owner metadata, invalid
     reservations, accepted, delayed, and rejected outcomes, dry-run
     authorization, committed holds, pending-capacity retry, release, cancel,
     expiration, active-state accounting, concurrency under a shared admission
     context, and no duplicate reservation database in QPM.
   - Reqs: `ADM-001`, `ADM-002`, `ADM-003`, `ADM-004`, `ADM-005`,
     `ADM-006`, `ADM-007`, `ADM-016`, `ADM-017`, `ADM-018`, `ADM-019`,
     `ADM-020`, `ADM-021`, `ADM-022`.

## Phase 6. Scheduler Integration And Managed Execution

Phase 6 moves normal reservation-scoped execution through `qhw-scheduler` and
dispatches only scheduler-selected work to QRC or provider adapters.

Primary requirements: `SCHED-001` through `SCHED-014`, `CAT-002`, `CAT-004`,
`API-001`, `API-004`.

1. PH6.1 Construct scheduler contexts per target.
   - Create one `qhw_sched_t` scheduler instance per QPM-managed execution
     target.
   - Use `QHW_SCHED_THREAD_SAFE` by default unless the target controller
     serializes all scheduler calls.
   - Keep scheduler policy, scheduler options, queue state, and selection state
     owned by qhw-scheduler.
   - Reqs: `SCHED-002`, `STATE-001`.

2. PH6.2 Implement scheduler control APIs.
   - Configure scheduler policy and options.
   - Implement queue-state inspection, pause, resume, drain, and dispatch depth
     controls.
   - Keep token placeholders on scheduler control APIs without checking them
     in this milestone.
   - Reqs: `CAT-004`, `CTRL-001`, `CTRL-006`, `SCHED-007`.

3. PH6.3 Insert only admitted qtasks into qhw-scheduler.
   - Call `qhw_sched_submit_task()` only after reservation validation,
     accepted qhw-admission capacity authorization, and accepted `consume()`
     have established an estimated capacity hold.
   - Record the scheduler task ID with reservation ID, QFw circuit ID, QPM
     qtask ID, admission usage key, and later provider handle.
   - Reqs: `SCHED-001`, `SCHED-003`, `SCHED-004`, `ADM-006`, `ADM-019`.

4. PH6.4 Dispatch only scheduler-selected qtasks.
   - Select work through qhw-scheduler before provider submission.
   - Bound provider queue depth using site policy or scheduler control
     configuration.
   - Store submitted-provider overlay state and provider handles in QPM runtime
     state.
   - Reqs: `SCHED-001`, `SCHED-005`, `SCHED-007`, `SCHED-009`,
     `STATE-003`.

5. PH6.5 Migrate `sync_run()` and `async_run()` to the shared managed path.
   - Require `reservation_id` and preserve the opaque token parameter for
     normal resource-affecting execution.
   - Make synchronous execution create the same managed qtask as asynchronous
     execution and block only according to the synchronous execution contract.
   - Return timeout status with task handles and lifecycle state when the wait
     expires, and cancel on timeout only when requested.
   - Reqs: `API-001`, `CAT-002`, `SCHED-008`, `SCHED-009`, `SCHED-010`.

6. PH6.6 Implement scheduler and admission completion ordering.
   - On provider completion, failure, cancellation, or timeout reconciliation,
     re-enter the controller before exposing terminal result state.
   - Return unused capacity and record actual usage in qhw-admission while the
     reservation remains active.
   - Update qhw-scheduler state to completed, failed, or cancelled as
     appropriate.
   - Publish completion events, completion-queue records, and terminal
     `sync_run()` results only after admission and scheduler state are updated.
   - Reqs: `ADM-007`, `ADM-021`, `SCHED-005`, `SCHED-006`, `STATE-004`.

7. PH6.7 Implement cancellation propagation.
   - Cancel pending-capacity entries, queued scheduler tasks,
     selected-but-not-submitted work, provider-submitted work, result state,
     event state, and admission accounting from one controller path.
   - Reconcile provider completion that races with cancellation.
   - Reqs: `SCHED-011`, `ADM-021`, `STATE-003`, `STATE-004`.

8. PH6.8 Implement managed task status and queue observations.
   - Expose pending capacity, queued, waiting, selected, submitted, running,
     completed, failed, cancelled, and timed-out states.
   - Derive queued, waiting, selected, running, completed, failed, and
     cancelled from qhw-scheduler where applicable.
   - Derive pending capacity, submitted, and timed-out overlays from QPM
     controller state.
   - Expose pending-queue position, scheduler queue position, scheduling order,
     estimated wait time, or estimated start time only when site policy and
     telemetry support it.
   - Reqs: `SCHED-012`, `SCHED-013`, `SCHED-014`, `API-004`, `CTRL-008`.

9. PH6.9 Add scheduler integration tests.
   - Cover admitted insertion, delayed capacity not entering scheduler,
     scheduler selection, bounded provider depth, shared sync and async path,
     synchronous timeout behavior, diagnostic bypass restrictions, completion
     accounting order, cancellation races, task status mapping, queue position,
     wait estimates, scheduler failures, and provider failures.
   - Reqs: `SCHED-001`, `SCHED-002`, `SCHED-003`, `SCHED-004`,
     `SCHED-005`, `SCHED-006`, `SCHED-007`, `SCHED-008`, `SCHED-009`,
     `SCHED-010`, `SCHED-011`, `SCHED-012`, `SCHED-013`, `SCHED-014`,
     `API-001`, `API-004`.

## Phase 7. Telemetry, Reconciliation, And Hardening

Phase 7 completes operational behavior, surfaces policy-filtered telemetry,
and removes compatibility paths that can bypass the managed-resource contract.

Primary requirements: `CAT-005`, `API-002`, `API-004`, `CTRL-002` through
`CTRL-008`, `STATE-004`, `SCHED-013`, `SCHED-014`.

1. PH7.1 Implement telemetry/discovery access partitioning.
   - Support basic discovery, caller-owned state, manager aggregate state, and
     operator telemetry as access classes inside the telemetry/discovery API.
   - Keep method, object, and field visibility labels in the API shape.
     This milestone records labels but does not enforce caller access.
   - Reqs: `CAT-005`, `API-002`, `CTRL-004`.

2. PH7.2 Implement admission capacity snapshots.
   - Provide pending qtask count, scheduler queue depth, estimated queued
     device time, active reservation count, held or in-flight capacity,
     available capacity or credits, current scheduler policy, device
     availability, timestamps, and confidence when available.
   - Expose the snapshot through a defined QPM or QPU control interface for
     qhw-admission policies instead of requiring policies to read QFw internals
     or qhw-scheduler internals directly.
   - Reqs: `CTRL-002`, `CTRL-003`, `CTRL-007`, `CTRL-008`.

3. PH7.3 Implement queue metrics and estimate telemetry.
   - Expose aggregate pending count, scheduler depth, estimated queued device
     time, active task count, held capacity, in-flight capacity, policy-specific
     scheduling state, wait estimates, and start estimates.
   - Label estimates with confidence, timestamp, and policy context when those
     values are available.
   - Report estimates as unavailable when the scheduler policy or telemetry
     cannot support a defensible value.
   - Reqs: `SCHED-013`, `SCHED-014`, `CTRL-007`, `CTRL-008`.

4. PH7.4 Add reconciliation and recovery.
   - Reconcile QPM restart, stale directory generations, provider handles,
     incomplete capacity holds, unfinished scheduler tasks, and pending
     capacity entries.
   - Emit reconciliation faults when held work is discovered after a
     reservation is already released, cancelled, or expired and normal
     active-state accounting can no longer be applied.
   - Reqs: `ADM-021`, `STATE-004`, `CTRL-004`.

5. PH7.5 Expose service lifecycle telemetry and audit records.
   - Report registration, deregistration, peer loss reason, timeout, restart,
     generation change, retention purge, diagnostic bypass, policy change, and
     reconciliation events.
   - Reqs: `DISC-001`, `CTRL-004`, `CAT-005`.

6. PH7.6 Remove managed-execution compatibility debt.
   - Remove deprecated QPM use of directory `reserve()` and `release()`.
   - Remove or strictly gate compatibility execution paths that can bypass
     admission authorization or scheduler selection.
   - Remove old DEFw four-list compatibility exports and Python view adapters
     after the unified C connection table and Python peer table are the only
     supported path.
   - Ensure public production execution requires reservation-scoped managed
     execution.
   - Reqs: `DISC-001`, `DISC-002`, `SCHED-009`, `SCHED-010`, `API-001`.

7. PH7.7 Add end-to-end and operations tests.
   - Cover QFw-managed and long-running modes, resolver behavior, reservation
     creation, reservation-scoped execution, scheduler dispatch, cancellation,
     timeout, release, expiration, telemetry filtering, capacity snapshots,
     reconciliation, structured error envelopes, unified DEFw connection table
     behavior, Python peer table behavior, SWIG callback behavior,
     build-system outputs, and compatibility removal.
   - Reqs: `OPM-001`, `OPM-002`, `OPM-003`, `DISC-001`, `DISC-002`,
     `DISC-003`, `DISC-004`, `DISC-005`, `ADM-001`, `ADM-002`, `ADM-003`,
     `ADM-004`, `ADM-005`, `ADM-006`, `ADM-007`, `ADM-016`, `ADM-017`,
     `ADM-018`, `ADM-019`, `ADM-020`, `ADM-021`, `ADM-022`,
     `SCHED-001`, `SCHED-002`, `SCHED-003`, `SCHED-004`,
     `SCHED-005`, `SCHED-006`, `SCHED-007`, `SCHED-008`, `SCHED-009`,
     `SCHED-010`, `SCHED-011`, `SCHED-012`, `SCHED-013`, `SCHED-014`,
     `CAT-001`, `CAT-002`, `CAT-003`, `CAT-004`, `CAT-005`, `CAT-006`,
     `CAT-007`, `API-001`, `API-002`, `API-003`, `API-004`, `CTRL-001`,
     `CTRL-002`, `CTRL-003`, `CTRL-004`, `CTRL-005`, `CTRL-006`,
     `CTRL-007`, `CTRL-008`, `STATE-001`, `STATE-002`, `STATE-003`,
     `STATE-004`.

## Requirement Coverage Summary

| Requirement group | Primary implementation phases |
| --- | --- |
| `OPM-001` through `OPM-003` | Phases 1, 2, and 7 |
| `DISC-001` through `DISC-005` | Phases 1, 2, and 7 |
| `ADM-001` through `ADM-022` | Phases 3, 5, 6, and 7 |
| `SCHED-001` through `SCHED-014` | Phases 4, 5, 6, and 7 |
| `CAT-001` through `CAT-007` | Phases 3, 4, and 7 |
| `API-001` through `API-004` | Phases 2, 3, 6, and 7 |
| `CTRL-001` through `CTRL-008` | Phases 3, 5, 6, and 7 |
| `STATE-001` through `STATE-004` | Phases 4, 5, 6, and 7 |
