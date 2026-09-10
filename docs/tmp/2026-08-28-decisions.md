# QFw v0.1 Design Decisions

Date: 2026-08-28

This document records decisions for the long-running QPM service design. New
decisions can be appended as the design discussion progresses.

## Decision 1: Trust the Slurm plugin identity during reservation

Status: Accepted for QFw v0.1

QFw v0.1 does not authenticate callers of the QPM reservation API. The Slurm
plugin calls `reserve` and supplies the username, allocation identifier, and
job information. QPMd treats the username supplied by the plugin as the user
identity for the reservation.

QPMd uses the username and requested device to find the corresponding entry in
`qpu-users.json`. A missing user or device entitlement causes immediate
rejection before qhw-admission is called. An entitled request proceeds through
qhw-admission, after which QPMd binds the provider credential associated with
the user and device. A credential-binding failure causes QPMd to roll back the
admission reservation and reject the request.

Once reservation setup succeeds, QPMd returns the reservation identifier to
the Slurm plugin. The plugin places that identifier in the application job's
environment. Subsequent application requests carry the reservation identifier
so QPMd can locate the reservation and its provider credential.

The plugin-provided identity is a deployment trust assumption rather than an
authenticated identity. QFw v0.1 does not enforce that only the Slurm plugin
can call plugin-facing APIs or prevent another caller from asserting a
username. Caller authentication and API-role enforcement remain outside the
v0.1 scope.

The reservation identifier is the application-visible correlation value for
this release. It is not described as a separate application authorization
token.

## Decision 2: Require entitlement and a configured API key

Status: Accepted for QFw v0.1

A user may reserve a QPU only when the matching user and device entitlement is
enabled and contains a non-empty API key. QPMd rejects the reservation when the
entitlement is disabled or the API key is absent. API-key presence is therefore
necessary but does not override a disabled entitlement.

QPMd performs both checks before calling qhw-admission. It reads the username
from the trusted Slurm plugin request described in Decision 1 and selects the
entry for the requested device in `qpu-users.json`. Only a request that passes
the entitlement and API-key checks reaches admission control.

The pre-admission API-key check verifies that usable credential material is
configured. QPMd binds that credential to the reservation only after
qhw-admission accepts the request. A failure during binding causes QPMd to roll
back the admission reservation and reject the request.

Dynamic API-key generation from refresh tokens is outside the QFw v0.1 scope.
That capability can extend credential binding without weakening the explicit
entitlement requirement.

## Decision 3: Declare the credential mode in the service manifest

Status: Accepted for QFw v0.1

Every QPM service-manifest entry declares its credential policy explicitly.
Credential-free simulators use `credential-mode: no-secret`. Hardware services
use `credential-mode: required` and identify their device with `device-id`.

A service using `required` resolves its credential provider from the matching
entry in `device-access.yaml`. QPM startup fails when the device identifier,
device-access configuration, or credential-provider configuration is missing,
unreadable, or incomplete. An absent `credential-mode` is also a configuration
error. Missing configuration never selects the no-secret provider implicitly.

Packaged NWQSim, TNQVM, and fakeIQM manifests declare the no-secret mode. The
native IQM service and the QPM shim declare the required mode. The shim is used
only with real hardware in QFw v0.1 and therefore always requires credentials.
This configuration change does not alter activation, service startup,
reservation, execution, or release commands used by operators and
applications.

Reservation processing continues to apply Decision 2. Hardware reservations
must pass the user entitlement and API-key checks before qhw-admission is
called. Credential binding occurs after admission succeeds and is rolled back
with the admission reservation if binding fails.

## Decision 4: Scope credential and IQM client caches by reservation

Status: Accepted for QFw v0.1

QPMd maintains a separate credential binding for each reservation. The binding
maps the reservation identifier to its provider credential and metadata. The
IQM client cache also uses the reservation identifier, together with device
identity where needed, instead of using the API-key value as its cache key.

Two reservations may contain separate in-memory bindings for the same API key.
Each reservation owns its IQM client. Releasing one reservation removes only
that reservation's binding and client; other reservations remain valid even
when they use the same underlying API key.

Release, cancellation, expiration, and QPM shutdown apply the same cleanup
sequence. QPMd first settles or cancels active operations for the reservation.
It then removes the binding while retaining a temporary reference, calls
`CredentialProvider.release(binding)`, evicts the reservation's IQM client,
and discards the remaining reservation-specific credential references.

Cleanup does not modify the site-managed credential in `qpu-users.json`. It
removes only state owned by the reservation. Python reference removal does not
guarantee immediate secure erasure of the underlying credential string from
process memory.

## Decision 5: Resolve every QPM through a directory service

Status: Accepted for QFw v0.1

Every QPM registers its endpoint and API bindings with a DEFw directory
service. A site-owned QPM registers with the persistent site directory service.
An application-owned QPM registers with the directory service started for that
application run. Clients obtain QPM connection information only through the
applicable directory service.

`QPMResolver` remains the client-side directory query, selection, and binding
layer. It queries the configured local or site directory scopes, applies the
requested provider and capability filters, rejects stale or ambiguous records,
and connects to the selected API binding.

The implementation will remove the direct-endpoint path that constructs QPM
connection information without querying a directory service. This cleanup
includes `DirectEndpointDirectory`, direct-endpoint resolver branches,
direct-only environment settings, and their associated tests and
documentation. Example reservation drivers continue to require directory
service connectivity.

## Decision 6: Export QPM and reservation identifiers as tuples

Status: Accepted for QFw v0.1

The Slurm plugin exports `QFW_RESERVATIONS` after every requested QPM
reservation has completed successfully. Its value is a JSON list of
`service_id` and `reservation_id` tuples. For example:

```bash
QFW_RESERVATIONS='[["iqm-ornl-20q","41"],["nwqsim-site","17"]]'
```

The `service_id` identifies the QPM registration to query through the directory
service. The corresponding `reservation_id` identifies the reservation to use
when calling that QPM. `QPMResolver` parses the tuples, resolves each exact
service through the configured directory, and associates the resulting API
binding with its reservation identifier.

Reservation identifiers are exported as decimal strings so JSON consumers do
not lose precision above `2^53`. The value remains text while the resolver and
DEFw route the request. The QPM and qhw-admission boundary validates the text
and converts it to `uint64_t`. Zero, non-decimal values, and values above
`UINT64_MAX` are invalid.

The same tuples are used for execution and release. QPM endpoints, runtime
identifiers, generations, and device metadata remain discoverable through the
directory service and are not duplicated in the application environment.

## Decision 7: Persist a per-QPM unsigned reservation sequence

Status: Accepted for QFw v0.1

QPMd allocates reservation identifiers from a persistent `uint64_t` sequence
owned by its stable `service_id`. Reserve callers do not select reservation
identifiers. QPMd supplies the allocated nonzero identifier to qhw-admission,
which already accepts caller-provided reservation identifiers.

The sequence high-water mark resides in protected, persistent QPM state that
survives service restarts. QPMd locks the state, chooses the next identifier,
and durably advances the high-water mark before presenting the identifier to
qhw-admission. Rejected requests may leave gaps in the sequence. Identifiers
are never reclaimed merely because a reservation was rejected or released.

QPMd looks for the sequence file in its run directory during startup. When the
file exists, QPMd resumes from its stored high-water mark. When it does not
exist, QPMd initializes the last-used value to zero, making 1 the first issued
reservation identifier. An existing file that is corrupt or unreadable
prevents QPM startup.

Site-owned and application-owned QPMs use the same behavior. A long-running
QPM preserves identifiers by reusing its run directory across restarts. A new
application run has a new run directory and begins from zero. A QPM that may
restart on another node uses a run directory available at the same path on
every eligible QPM node.

After `UINT64_MAX`, allocation wraps to 1. Zero remains reserved and is never
issued. Allocation skips any identifier that belongs to an active reservation.
This policy permits historical identifier reuse only after the complete
64-bit namespace has been exhausted. At one allocation per second, that event
would occur after roughly 584 billion years. The theoretical reuse behavior is
an accepted QFw v0.1 limitation.

## Decision 8: Allow one reservation per QPM service per application

Status: Accepted for QFw v0.1

An application may reserve several QPM services, but it reserves each
`service_id` at most once during its lifetime. `QFW_RESERVATIONS` therefore
contains no duplicate service identifiers. Each tuple uniquely associates one
reserved QPM service with one reservation identifier.

The Slurm plugin enforces this invariant while constructing the reservation
set. `QPMResolver` treats duplicate `service_id` entries as invalid input. No
selection policy is needed to choose between two reservations for the same QPM
service because that state cannot be created.

## Decision 9: Permit applications to view a sanitized service catalog

Status: Accepted for QFw v0.1

Applications may query the DEFw directory service and view all registered QPM
services available through that directory. Discovery is not authorization.
QPMd validates the reservation identifier before performing a managed
operation, regardless of what the directory returned.

Directory records may expose service identifiers, providers, device types,
capabilities, registration generations, liveness, and connection information
needed by DEFw clients. They do not expose provider credentials,
credential-store paths, reservation identifiers, user credential metadata, or
workload contents.

Reservation ownership and aggregate allocation status are separate from QPM
registration. A site may expose that information through a sanitized
admission, scheduler, or telemetry interface according to site policy. It does
not belong in the static directory registration record.

## Decision 10: Require service IDs to be unique across visible directories

Status: Accepted for QFw v0.1

A `service_id` identifies one active QPM service across every directory scope
visible to an application. Local, site, and hybrid resolution do not qualify a
service identifier with a directory scope. Two visible QPM registrations
therefore cannot use the same `service_id`.

Site administrators assign stable, site-wide identifiers to long-running QPM
services. Application-owned QPMs receive identifiers unique to their
application run. This prevents a hybrid resolver from confusing a local QPM
with a site QPM that uses the same backend type or manifest entry name.

The `service_id` in each `QFW_RESERVATIONS` tuple is sufficient to select one
directory registration. A lookup that finds the identifier in more than one
visible directory is a configuration error and does not select by directory
ordering.

## Decision 11: Separate QPM discovery from reserved resolution

Status: Accepted for QFw v0.1

QFw exposes two distinct resolver operations. General discovery searches the
directory catalog for QPM services that satisfy provider, device, capability,
and availability criteria. The Slurm plugin and administrative tools use this
operation before a reservation exists.

Reserved resolution serves application execution after Slurm has completed
the reservation step. It accepts an exact `service_id` from
`QFW_RESERVATIONS`, obtains that service's current directory registration, and
returns the requested API binding together with the corresponding reservation
identifier.

General discovery never grants access to a QPM. Reserved resolution does not
repeat candidate selection or substitute another compatible service when the
reserved service is unavailable. QPMd remains responsible for validating the
reservation on every managed operation.

## Decision 12: Invalidate active reservations when QPMd restarts

Status: Accepted for QFw v0.1

QPMd does not reconstruct active qhw-admission reservations, credential
bindings, provider clients, or in-flight operations after a process restart.
Every reservation held by the previous QPM runtime becomes invalid.

The restarted QPM registers a new runtime identity with the directory service.
The directory assigns its generation according to Decision 15. Requests using
an old reservation fail runtime-generation or QPM reservation validation.
Slurm must fail the affected application operation or acquire a new
reservation through the normal reservation flow.

The persistent sequence described in Decision 7 survives the restart. The
next reservation receives a later identifier, which prevents an old tuple
from matching newly created reservation state.

## Decision 13: Apply entitlement and key changes to new reservations

Status: Accepted for QFw v0.1

Entitlement configuration is fail-closed. Both the user-level `enabled` field
and the requested device's `enabled` field must exist and be `true`. A missing
field has the same effect as `false`. The device entry must also contain the
non-empty API key required by Decision 2.

QPMd evaluates this configuration when processing a new reservation. Changes
to an entitlement or API key affect reservations requested after the change.
They do not revoke an active reservation or replace the credential already
bound to it. Active revocation and live credential rotation require a separate
runtime policy and are outside the QFw v0.1 contract.

## Decision 14: Complete reservation closure after release-hook failure

Status: Accepted for QFw v0.1

A failure from `CredentialProvider.release()` does not reactivate a
reservation or leave it available for new work. QPMd records and reports the
cleanup failure, evicts the reservation-scoped provider client, and removes
its in-memory credential references. Admission and controller state remain
closed.

Credential-provider callbacks run outside the controller lock. A slow or
unavailable external provider therefore cannot block unrelated QPM state
transitions. Providers that manage external credential state may retry failed
cleanup through provider-owned recovery logic without reopening the
reservation.

## Decision 15: Use one runtime UUID for one QPM service incarnation

Status: Accepted for QFw v0.1

Each QPMd process hosts exactly one QPM service. DEFw generates a fresh UUID
when that process starts, and QPMd registers the UUID with the directory as its
`runtime_id`. The UUID therefore identifies one incarnation of one QPM
service. Restarting QPMd retains its stable `service_id` and receives a new
`runtime_id`.

The directory generation is an in-memory incarnation counter. Re-registering
a service while its previous directory record remains available increments
the generation. Restarting the directory service or allowing an inactive
record to expire may reset the generation to 1.

QFw v0.1 accepts this generation reset behavior. Runtime UUIDs distinguish QPM
process incarnations, while QPM reservation validation rejects reservation
state belonging to a previous incarnation. Persistent directory generations
are not required for this release.

## Implementation Status Audit

Audit date: 2026-08-28

This section separates accepted design from implementation work. An
`Existing` decision requires validation but no new functional implementation.
A `Partial` decision has foundations that must be preserved while its listed
gaps are implemented. `Needs implementation` means the decision's runtime
contract is absent even when lower-level supporting primitives exist.

### Decision 1: Partial

The admission API already accepts caller-supplied owner, user, job, and
allocation metadata without authenticating the caller. QPMd records this
metadata as externally supplied identity. The example Slurm driver exercises
that trust model.

The production Slurm reservation integration is not present. The existing
driver identifies itself as a stand-in for that integration. Slurm still needs
to call reserve and release as the trusted launcher and export the completed
reservation context. The entitlement ordering belongs to Decision 2.

### Decision 2: Partial

The file credential provider already rejects a missing API key. QPMd also
rolls back qhw-admission when credential binding fails after admission.

User and device `enabled` fields are not evaluated. Credential lookup occurs
after qhw-admission, so the required pre-admission entitlement and API-key
check still needs implementation.

### Decision 3: Needs implementation

Service manifests already support `device-id`, and QPM startup can select a
site `device-access.yaml`. The manifests do not define `credential-mode`, and
the manifest loader does not validate it.

Credential-provider selection can still infer `NoSecretCredentialProvider`
when configuration is absent. The explicit `required` and `no-secret` modes,
their packaged service assignments, and fail-closed startup validation all
need implementation.

### Decision 4: Partial

QPMd already stores credential bindings in
`reservation_credentials_by_id`, and execution retrieves a binding through
the reservation identifier. This per-reservation map must remain.

The binding does not retain the provider instance that created it, provider
`release()` is never called, and IQM clients are cached by credential values
that include the API key. Reservation-keyed IQM clients and cleanup on every
close path still need implementation.

### Decision 5: Partial

Application-owned and site-owned QPM registration already exists. The DEFw
directory records runtime identity and generation, while `QPMResolver`
supports local and site directory scopes, stale-generation checks, and API
binding connections.

Direct QPM resolution remains implemented through
`DirectEndpointDirectory`, direct resolver scope, launcher configuration,
tests, and documentation. Removing that path and requiring registration for
every QPM remains implementation work.

### Decision 6: Needs implementation

The examples export the singular `QFW_RESERVATION_ID`. Neither the runtime nor
`QPMResolver` parses `QFW_RESERVATIONS` tuples or associates resolved services
with their reservation identifiers.

Decimal-string validation and conversion to `uint64_t` at the QPM and
qhw-admission boundary also need implementation. DEFw directory records
already provide the endpoint, runtime identity, generation, and metadata that
the environment should not duplicate.

### Decision 7: Partial

qhw-admission already represents reservation identifiers as `uint64_t` and
accepts a caller-supplied nonzero identifier. This native capability must be
reused.

QPMd does not allocate or persist reservation identifiers. It passes zero by
default and lets each in-memory qhw-admission context allocate from its own
counter. The per-QPM sequence file, locking, durable high-water update,
startup recovery, and wrap handling need implementation in QPMd.

### Decision 8: Needs implementation

The one-reservation-per-service invariant is not represented because
`QFW_RESERVATIONS` does not exist. The Slurm integration and resolver need to
enforce one tuple per `service_id` for an application lifetime.

The service-plane manager already limits one service-plane QPM instance to one
selected service. That process-launch invariant is separate and does not
implement the application reservation invariant.

### Decision 9: Partial

DEFw already exposes registered QPM service records through directory
resolution. Current QPM registrations contain identity, endpoint, API
bindings, capabilities, generation, liveness, and aggregate controller
metadata. Managed QPM operations already require reservation validation.

The directory accepts an open-ended service `properties` mapping and has no
explicit catalog sanitization or allowlist. The catalog boundary still needs
to enforce that credentials, credential paths, user metadata, reservation
identifiers, and workload contents cannot enter application-visible records.

### Decision 10: Partial

A single DEFw directory already rejects a second live runtime using the same
`service_id`. This protects uniqueness within one directory instance.

Uniqueness is not enforced across local and site directories visible to a
hybrid resolver. Packaged local and site manifests reuse identifiers such as
`nwqsim` and `fake-iqm`, and application-owned QPMs use the static manifest
name rather than a run-unique service identifier. Cross-scope validation and
run-unique application service IDs need implementation.

### Decision 11: Partial

General capability-based discovery exists in `QPMResolver`, and the DEFw
directory can filter directly by `service_id`.

`QPMResolutionRequest` does not accept `service_id`, and no reserved-resolution
operation consumes `QFW_RESERVATIONS`. The separate exact-service operation
and its no-substitution behavior need implementation.

### Decision 12: Partial

A restarted QPM already receives a fresh DEFw runtime UUID and a fresh
in-memory controller and qhw-admission context. Old reservations are therefore
absent and managed operations reject them. Directory generation checks also
reject a binding that becomes stale during resolution.

The persistent sequence from Decision 7 is missing. The production Slurm
integration also lacks the policy that fails an affected operation or obtains
a new reservation after QPM restart. Those portions need implementation.

### Decision 13: Partial

Credential material is copied into a reservation-specific binding, so later
file changes do not automatically replace the credential held by an active
reservation. This already provides the selected v0.1 behavior for active key
rotation.

The credential database has no implemented user or device `enabled` checks.
Missing flags are not treated as disabled. Fail-closed entitlement evaluation
for new reservations needs implementation.

### Decision 14: Partial

Accepted release, cancellation, and expiration paths already close admission
state and remove the QPM credential mapping. Shutdown clears the in-memory
credential map. These closure behaviors must remain.

QPMd does not retain the creating provider, call provider `release()`, evict a
reservation-owned IQM client, or report provider cleanup failures. Provider
callbacks also need to move outside the controller lock. These cleanup and
failure-handling behaviors need implementation.

### Decision 15: Existing

The service-plane manager enforces one selected QPM service per process. DEFw
generates a new UUID at process startup, QPM registration records it as
`runtime_id`, and the configured `service_id` remains stable across a restart.

The directory already increments generations while the prior record remains
available and permits them to reset after record expiration or directory
restart. No functional implementation is needed for this decision.
