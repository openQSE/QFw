# QFw v0.1.0-rc.1 Release Notes

## Overview

QFw 0.1 combines the main development line with the admission and scheduling
work developed on `adm-sched-v01`. It is a coordinated candidate across QFw,
DEFw, qhw-data, qhw-iqm, qhw-admission, and qhw-scheduler. The candidate adds a
managed quantum workload lifecycle from reservation through provider dispatch,
completion, telemetry, and release, together with reproducible CMake-based
source and installed runtimes.

## Admission, scheduling, and reservations

- QFw now creates target-scoped admission and scheduler contexts and routes
  tasks through both before provider dispatch.
- Reservations carry owner, allocation, workload, scope, timing, capacity,
  and provider credential context. Capacity is restored on completion,
  cancellation, failure, and release.
- qhw-admission adds filtered and paginated authoritative reservation listing
  and exposes provider queue depth in device profiles.
- qhw-scheduler packages its existing FIFO, priority, ordered SJF/LJF,
  round-robin, split, and composed policy support for installed use.
- Dispatch limits account for QPM concurrency and provider queue depth. QPM
  telemetry exposes reservation, queue, capacity, scheduler, and lifecycle
  state at the appropriate access level.

## QPM API and lifecycle

The former monolithic QPM surface is separated into common, execution,
control, admission-control, admission-policy configuration,
scheduler-control, and telemetry categories. Bindings advertise their
categories and versions. Request and metadata tokens, category authorization,
target and capability selectors, and privileged lifecycle controls make
service discovery and operation explicit.

Managed tasks have reservation and scheduler identities, provider handles,
completion queues, terminal retention, retry handling, and reconciliation
state. Long-running site QPMs remain operator-owned while application clients
connect, submit, receive completions, and disconnect independently.

## Qiskit and provider integrations

Qiskit lookup resolves services through QPM selectors and carries reservation
context into jobs, samplers, and estimators. QFw jobs register completion
events and do not shut down shared site services.

IQM support includes owner-to-credential binding, device and telemetry
preflight, request transcoding, native result normalization, chemistry
wrappers, and long-running site-service management. Runtime service-directory
ports are isolated from existing installations. An explicitly selected
service run directory now takes precedence over activation defaults.

NWQSim statevector results are normalized even when counts are absent. VQE
preserves logical statevector dimensions and rejects inconsistent result
sizes. Release examples use the stable non-DVM NWQSim path for repeated
statevector workloads.

The stack also retains shim, fake-IQM, QB, TNQVM, QDMI, and QRMI service code.
The release validation directly exercised shim, fake-IQM, NWQSim, and real IQM
paths.

## DEFw integration

DEFw now builds with CMake and installs libraries, launchers, Python runtime,
configuration, CMake metadata, SWIG modules, and public typemaps. Directory
service bindings replace the legacy resource-manager discovery path. Peer
identity, readiness, loss, removal, and service registration have explicit
lifecycle handling.

The merge retains the TCP/libfabric transport abstraction, OFI address
exchange, tagged RPC transport, optional RMA attachments, and UUID-based
sender identity from the default line. Inactive connection descriptors are
guarded during partial startup and teardown.

## Build, installation, and configuration

The top-level QFw CMake project can bundle DEFw and stages the validated
qhw-admission and qhw-scheduler packages. It generates activation, setup,
service-start, Slurm-launch, and teardown commands and validates both source
and installed runtimes.

Site, runtime, and service YAML replace the legacy setup fragments. Local and
site services can use distinct directory endpoints, explicit virtual
environments, bounded readiness checks, and repeatable runtime directories.
Installation excludes development credential configuration and does not copy
live device credentials.

Use these managed commands in place of removed setup fragments:

```text
qfw-activate
qfw-setup
qfw-srun
qfw-teardown
qfw-dirsvc-start
qfw-service-start
```

Native consumers must rebuild against this compatibility set because
`qhw_adm_device_profile_t` gains `max_provider_queue_depth`.

## Examples and test tooling

Examples now share managed setup and teardown, emit machine-readable JSONL,
and include reservation-aware Slurm drivers. The release adds long-running QPM
waves, bounded fake-IQM stress matrices, IQM chemistry helpers, and stronger
init, MPI, shim, Qiskit, PennyLane, QAOA, VQE, and SupermarQ wrappers.

The fake-IQM suites cover startup, admission decisions, scheduler ordering,
concurrent workers, hybrid waves, completion accounting, capacity restoration,
and leak detection without requiring a secret.

## Coordinated component set

| Component | Commit |
| --- | --- |
| qhw-data | `63a24c88739a35bdafab3ef2cea88908f0845fb3` |
| qhw-iqm | `e3078979455188e1bda41ac25e280d92214a7d1c` |
| qhw-admission | `47582500fcb1b06f3d32dad6aa78604dfbda67dd` |
| qhw-scheduler | `8cf431d6d64a844dbe04646711f566f7c36572bc` |
| DEFw | `7728f89673efb96391e6880139ecccc8ce324f1b` |
| QFw code | `f45fcd8772a759abd8feab933eef8ea2a837ffbc` plus this release-metadata commit |

Every dependency commit above is the exact QFw gitlink and is reachable from
its authoritative `release/v0.1` branch. All components use the planned
annotated tag `v0.1.0-rc.1`; QFw is tagged last.

## Validation summary

- Component package, native, plugin, static, install, and schema checks passed.
- QFw reports 192 passed mock tests with one intentional skip, 28 runtime
  tests, one Qiskit target test, and 15 passed bundled QFw/DEFw CTests.
- A clean recursive GitHub clone rebuilt and installed the same component set.
- All ten standard examples passed in QFw-SLURM-Cluster-Doug.
- A two-application, two-wave long-running NWQSim QPM run passed.
- All bounded fake-IQM scenario sets passed without leaks.
- Real-IQM telemetry and credential preflight passed. The chemistry smoke
  workflow completed 21 QFw IQM tasks, released its reservation, and tore down
  cleanly.
- Protected credential metadata and checksum were unchanged before and after
  testing, and the release installation contains no credential file.

## Known limitations

- The validation hosts did not provide libfabric development headers or
  pkg-config metadata. TCP transport is fully tested; compiled OFI/RMA
  validation remains required on suitable infrastructure.
- The chemistry application owns its Python dependencies. OpenFermion is not
  installed by QFw and was added to the isolated hardware-test environment.
- The real-IQM workflow used smoke settings and validates orchestration and
  hardware execution, not scientific convergence or production accuracy.
- Long-running QPM examples require a real Slurm allocation; running them
  directly outside an allocation is unsupported.
- These repositories do not have release-branch-triggered GitHub Actions.
  Clean-clone builds provide the pre-tag reproducibility gate. Tag-triggered
  CI and release artifacts must still pass after publication.
- Existing compiler and SWIG ownership warnings remain visible but did not
  fail the validated builds.
