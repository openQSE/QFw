# Detailed Design

## Table Of Contents

- [Purpose](#purpose)
- [Design Context](#design-context)
- [Requirement Design Notes](#requirement-design-notes)

<details open>
<summary><strong>Purpose</strong></summary>

## Purpose

This document records implementation-oriented design notes for
`docs/requirements.md`. Each requirement has a matching collapsible subsection
identified by the same requirement ID.

</details>

<details open>
<summary><strong>Design Context</strong></summary>

## Design Context

The current QFw client path discovers QPM through DEFw-resmgr. The Qiskit
lookup path asks DEFw-resmgr for QPM service metadata, then calls
`defw.connect_to_resource()` to create the QPM client binding. The current
DEFw-resmgr `reserve()` path also performs local `DEFwServiceInfo` capacity
accounting and calls the service `reserve()` callback before returning service
endpoints.

The target design separates these concerns. DEFw-resmgr remains useful for
registered-service discovery, while QPM owns the active reservation flow and
uses qhw-admission as the authoritative reservation store. Long-running QPM
services remain DEFw-wrapped RPC services, but they need a startup mode that
does not require registration with DEFw-resmgr.

Relevant current implementation points:

- `backends/qfw_qiskit/qfw_lookup_service.py` discovers QPM by calling
  `rmgr.get_services("QPM", ...)`.
- `DEFw/python/infra/defw.py` implements `connect_to_resource()` by calling
  `resmgr.reserve()`, connecting to returned endpoints, and constructing the
  service API wrapper.
- `DEFw/python/services/svc_resmgr/svc_resmgr.py` implements
  `get_services()`, `reserve()`, and `release()`.
- `DEFw/python/infra/defw_agent_baseapi.py` currently uses the service
  `reserve()` callback as part of service activation.
- `services/util/qpm/util_qpm.py` currently owns QPM execution submission,
  local host-slot accounting, completion queues, and event registration.

</details>

<details open>
<summary><strong>Requirement Design Notes</strong></summary>

## Requirement Design Notes

<details>
<summary><strong>OPM-001</strong></summary>

### OPM-001

QFw-managed mode should preserve the current launch pattern: QFw starts the
DEFw resource manager and starts QPM services described by QFw service
configuration. QPM services register with DEFw-resmgr so existing clients can
continue using `rmgr.get_services("QPM", type, capability)` followed by QPM API
construction.

The design change is that registration is only discovery metadata. Admission
reservation semantics should be removed from the DEFw-resmgr service-selection
path and implemented inside QPM.

</details>

<details>
<summary><strong>OPM-002</strong></summary>

### OPM-002

Long-running mode should allow a QPM service to start as a DEFw-wrapped service
with a stable listening endpoint and without registering with DEFw-resmgr. QFw
initialization should be able to read the endpoint from configuration and build
a compatible QPM client binding.

The service still uses DEFw RPC once the client knows the endpoint. This avoids
turning the long-running service into a separate non-DEFw protocol while
removing the requirement that a DEFw-resmgr always be present.

</details>

<details>
<summary><strong>OPM-003</strong></summary>

### OPM-003

Deployment ownership should be orthogonal to reservation ownership. Whether QFw
starts a QPM service or connects to an existing QPM service, reservation calls
should enter QPM and then qhw-admission. This keeps admission behavior stable
across both deployment modes.

</details>

<details>
<summary><strong>DISC-001</strong></summary>

### DISC-001

DEFw-resmgr should continue to track registered agents and registered services.
Its useful responsibilities are registration, deregistration, metadata refresh,
and service filtering by service name, type, and capability.

The existing `get_services()` implementation already aligns with this model: it
refreshes registered service metadata and returns matching `DEFwServiceInfo`
records.

</details>

<details>
<summary><strong>DISC-002</strong></summary>

### DISC-002

The current `svc_resmgr.reserve()` implementation calls
`service_info.consume_capacity()` before activating the service callback. That
capacity is stored on the queried `DEFwServiceInfo` object, not in an
admission-grade resource database. The target design should remove QPM
admission capacity accounting from this path.

If DEFw still needs a connection lifecycle hook, it should be named and modeled
as activation/binding rather than reservation.

</details>

<details>
<summary><strong>DISC-003</strong></summary>

### DISC-003

DEFw service startup should gain an option that disables registration with
DEFw-resmgr while still starting the service listener. The option should be
explicit so accidental unregistered services are easy to diagnose.

Candidate configuration fields:

| Field | Meaning |
| --- | --- |
| `register-with-resmgr` | Boolean controlling whether the service registers with DEFw-resmgr. |
| `listen-endpoint` | Stable endpoint or port used by long-running clients. |
| `resmgr-endpoint` | Optional DEFw-resmgr endpoint for QFw-managed registration. |

</details>

<details>
<summary><strong>DISC-004</strong></summary>

### DISC-004

QFw should add a QPM resolver layer between clients and QPM discovery. The
resolver can keep the current DEFw-resmgr lookup path for QFw-managed services
and add a configured-endpoint path for long-running QPM services.

For a long-running QPM service, the resolver cannot infer the listening port.
The site or resource owner must publish an endpoint descriptor, either in QFw
configuration or through a future external registry. A first implementation can
use explicit static configuration:

```yaml
qpm-endpoints:
  - name: iqm-ornl-20q
    service-name: QPM
    provider: iqm
    device-id: ornl-iqm-20q
    address: qpm-host.example.org
    listen-port: 8095
    agent-name: qpm_iqm
```

The descriptor should identify the DEFw-wrapped QPM listener and the resource
it represents. It should not require a persisted DEFw UUID, because DEFw UUIDs
are process identity and may change when the long-running service restarts.

The configured-endpoint resolver path should:

1. Read the QPM endpoint descriptor.
2. Create a provisional DEFw endpoint from address, listen port, agent name,
   and service node type.
3. Connect to the DEFw-wrapped service.
4. Reload active service-agent state so DEFw learns the service's current
   remote UUID and block UUID from the connection handshake.
5. Query the connected service for QPM service metadata.
6. Verify that the service metadata matches the requested service name,
   provider, device ID, type, and capabilities.
7. Return the same QPM client binding shape used by the DEFw-resmgr discovery
   path.

After resolution, reservation and release behavior should be identical for
QFw-managed and long-running QPM services. The only difference is how QFw finds
the DEFw-wrapped QPM endpoint.

</details>

<details>
<summary><strong>DISC-005</strong></summary>

### DISC-005

The long-running QPM service should remain a DEFw service module with a DEFw
service API wrapper. The endpoint resolution mechanism changes, but callers
should still communicate through DEFw RPC and the QPM service API surface.

</details>

<details>
<summary><strong>ADM-001</strong></summary>

### ADM-001

QPM should treat qhw-admission as the source of truth for reservation identity
and lifecycle. The qhw-admission API already models reservation IDs, user IDs,
job IDs, device IDs, scope IDs, reservation state, usage, compliance, and
actual usage records.

QPM should not reimplement these records. It should translate QFw request data
into qhw-admission request and usage structures.

</details>

<details>
<summary><strong>ADM-002</strong></summary>

### ADM-002

QPM should expose a reservation API that accepts a QFw-level reservation
request, constructs a qhw-admission request, calls qhw-admission `reserve()`,
and returns the admission decision. An accepted decision includes the
reservation ID that later resource-affecting QPM calls must supply.

</details>

<details>
<summary><strong>ADM-003</strong></summary>

### ADM-003

QPM should expose a release API that accepts a reservation ID and reason code
when available. It should call qhw-admission `release()` and then clean local
transient execution state associated with that reservation.

Release is an admission lifecycle operation, not a DEFw-resmgr deregistration
operation.

</details>

<details>
<summary><strong>ADM-004</strong></summary>

### ADM-004

QPM may cache or index transient execution objects, but it should not maintain a
second durable reservation database. If QPM needs reservation details, it should
query qhw-admission using the reservation ID.

</details>

<details>
<summary><strong>ADM-005</strong></summary>

### ADM-005

Before QPM performs resource-affecting work, it should call the relevant
qhw-admission API for the supplied reservation ID. Examples include
authorization before execution, consumption when work is admitted to execution,
returning unused usage when work is cancelled, and recording actual usage after
completion.

</details>

<details>
<summary><strong>API-001</strong></summary>

### API-001

Current `api_qpm` execution calls such as `sync_run(info)` and
`async_run(info)` do not require a reservation ID. The target API shape should
add reservation-scoped execution calls or extend the request payload so the
reservation ID is always present for resource-affecting operations.

Compatibility can be handled by an explicit transition path, but the target
behavior should require the reservation ID.

</details>

<details>
<summary><strong>API-002</strong></summary>

### API-002

Read-only service metadata calls can remain available before reservation when
site policy permits. Current examples include backend information, device
information, calibration snapshots, and coupling graph queries.

Policy may later restrict some metadata calls. The QPM API should not assume
that every read-only call is always public.

</details>

<details>
<summary><strong>API-003</strong></summary>

### API-003

Long-running QPM services can serve multiple independent jobs over the same
service instance. Reservation IDs provide the stable caller-visible key for
distinguishing those jobs and sessions. QPM request handling should pass that
reservation ID through admission checks and attach it to transient execution
state where needed.

</details>

<details>
<summary><strong>API-004</strong></summary>

### API-004

Once a QPM client binding is resolved, reservation and release behavior should
be identical in QFw-managed mode and long-running mode. Differences should stay
inside the resolver and service startup paths.

</details>

<details>
<summary><strong>STATE-001</strong></summary>

### STATE-001

QPM already maintains transient runtime state such as circuit IDs, circuit
objects, completion queues, event notification metadata, and QRC/provider worker
state. This state belongs in QPM because it represents active execution, not
reservation policy.

</details>

<details>
<summary><strong>STATE-002</strong></summary>

### STATE-002

When transient execution state is created for reservation-scoped work, QPM
should store or derive the reservation ID alongside the execution object. This
lets QPM route status, completion, cancellation, cleanup, scheduler updates,
and admission accounting through the correct reservation.

</details>

<details>
<summary><strong>STATE-003</strong></summary>

### STATE-003

QPM cleanup should be tied to both execution terminal states and reservation
lifecycle events. When a reservation is released, cancelled, or expired, QPM
should stop accepting new resource-affecting work for that reservation and
clean local transient state when it is safe to do so.

</details>

</details>
