# Slurm Quantum Plugin Detailed Design

**Status:** draft

## DES-SLURM-001. Design Scope and Dependency Direction

This design implements the Slurm quantum lifecycle through two cooperating
processes. A native SPANK plugin owns Slurm callbacks, option processing,
reservation transaction order, job environment updates, and allocation
cleanup. A persistent gateway owns the temporary C-to-Python boundary. The
gateway speaks a small native protocol to the plugin and uses existing DEFw
bindings to call the directory service and QPMd.

The dependency direction is:

```text
Slurm
    -> native SPANK plugin
        -> QSGP client
            -> qfw-slurm-gateway
                -> DEFw directory client
                -> QPM admission-control client
                    -> qhw-admission
```

QFw does not import or build the Slurm plugin. The plugin repository consumes
an installed QFw environment only for the gateway. The Slurm-cluster
repository installs and configures released artifacts but contains no plugin
implementation.

The command-line interface uses provider-neutral quantum terms. The QFw
adapter is selected by gateway deployment configuration rather than by
QFw-specific workload option names.

**Requirements:**
- SLPARCH-001
- SLPARCH-002
- SLPARCH-003
- SLPARCH-004
- SLPARCH-007

**Status:** draft

## DES-SLURM-002. Repository and Package Layout

The separate OpenQSE repository uses the following ownership layout:

```text
qfw-slurm/
  CMakeLists.txt
  include/qsgp/
    qsgp_protocol.h
    qsgp_types.h
  src/plugin/
    spank_quantum.c
    options.c
    plugin_config.c
    reservation_transaction.c
    environment.c
  src/protocol/
    qsgp_encode.c
    qsgp_decode.c
    qsgp_io.c
    qsgp_munge.c
  gateway/qfw_slurm_gateway/
    __main__.py
    server.py
    protocol.py
    authentication.py
    slurm_verifier.py
    defw_client.py
    journal.py
  config/
    plugin.conf.example
    gateway.yaml.example
  systemd/
    qfw-slurm-gateway.service.in
  tests/
    unit/
    protocol/
    gateway/
    slurm/
```

The C protocol library is private to this repository during the gateway
phase. It is not installed as a general QFw client API. The gateway Python
package is installed into the site QFw virtual environment or into a dedicated
service environment containing compatible QFw and DEFw packages.

The build produces `spank_quantum.so`, protocol unit tests, and gateway package
artifacts. Building the plugin requires Slurm headers and libmunge. It does not
require Python headers or QFw source.

**Requirements:**
- SLPARCH-001
- SLPARCH-002
- SLPARCH-003
- SLPVAL-008

**Status:** draft

## DES-SLURM-003. Site Configuration

The plugin reads `/etc/qfw-slurm/plugin.conf`. The file is root-owned and not
writable by application users. It contains no provider credential.

```ini
[gateway]
host=slurmctld
port=18095
connect_timeout_ms=5000
request_timeout_ms=120000
max_credential_bytes=65536
expected_munge_uid=qfw-slurm

[resource "ornl-iqm-20q"]
service_id=iqm-ornl-20q

[resource "nwqsim"]
service_id=nwqsim-site
```

The gateway reads `/etc/qfw-slurm/gateway.yaml`. It selects the listening
address, QFw activation environment, site configuration, protected journal,
accepted plugin identities, Slurm verifier, and protocol limits.

```yaml
listen:
  host: 0.0.0.0
  port: 18095
  max-credential-bytes: 65536
  request-timeout-seconds: 120
authentication:
  mechanism: munge
  accepted-uids: [0]
  expected-plugin-name: spank_quantum
slurm:
  cluster-name: qfw-cluster
  verifier: scontrol-json
qfw:
  activation: /opt/openqse/qfw/bin/qfw-activate
  venv: /opt/openqse/qfw-venv
  site-config: /etc/openqse/qfw/site.yaml
journal:
  path: /var/lib/qfw-slurm-gateway/reservations.sqlite3
```

The plugin mapping is authoritative for converting `--qpu` into an exact
service ID. The gateway verifies that registration but does not perform
compatible-service selection. Changes to endpoint addresses, service runtime
identities, and generations remain directory-service concerns.

**Requirements:**
- SLPADM-001
- SLPADM-002
- SLPSEC-003
- SLPSEC-005
- SLPFAIL-001

**Status:** draft

## DES-SLURM-004. Plugin Internal State

Each loaded plugin instance holds only process-local parsing and request state:

```c
struct quantum_options {
    bool active;
    char *qpu_names;
    enum workload_kind workload_kind;
    uint64_t circuit_count;
    uint32_t max_qubits;
    uint64_t max_depth;
    uint64_t max_shots;
    uint64_t max_one_q_gates;
    uint64_t max_two_q_gates;
    uint64_t max_measurements;
    uint32_t present_fields;
};

struct plugin_context {
    struct quantum_options options;
    struct plugin_config config;
    struct reservation_vector accepted;
    char deferred_error[4096];
    bool launch_failed;
};
```

The code does not assume that this state survives between SPANK contexts.
Allocator, remote, and job-script callbacks may execute in different address
spaces. The gateway journal supplies cross-context correlation. No provider
token or QPU credential enters plugin memory.

Every allocation is identified by the configured Slurm cluster name and its
canonical job ID. A heterogeneous component carries its component job ID and
component index as additional diagnostics. The leader job ID remains the
allocation key so one service cannot be reserved independently by two
components of the same application.

**Requirements:**
- SLPARCH-005
- SLPARCH-008
- SLPLIFE-008
- SLPLIFE-009
- SLPSEC-006

**Status:** draft

## DES-SLURM-005. Option Registration and Validation

`slurm_spank_init()` registers every option in allocator, local, and remote
contexts. The same table is used by `salloc`, `sbatch`, `srun`, and
`slurmstepd`.

| Option | Parser | Validation |
| --- | --- | --- |
| `--qpu` | Resource-name list | Every name is nonempty, unique, length-bounded, and present in root-owned configuration. |
| `--workload-kind` | Enumeration | Exactly `quantum` or `hybrid`. |
| `--circ-count` | Unsigned decimal | Present, nonzero, and representable as `uint64_t`. |
| `--max-qubits` | Unsigned decimal | Present, nonzero, and representable as `uint32_t`. |
| `--max-depth` | Unsigned decimal | Present, nonzero, and representable as `uint64_t`. |
| `--max-shots` | Unsigned decimal | Present, nonzero, and representable as `uint64_t`. |
| `--max-one-q-gates` | Unsigned decimal | Optional and representable as `uint64_t`; zero is valid. |
| `--max-two-q-gates` | Unsigned decimal | Optional and representable as `uint64_t`; zero is valid. |
| `--max-measurements` | Unsigned decimal | Optional and representable as `uint64_t`; zero is valid. |

Parsing uses checked conversion rather than `atoi()`. Leading signs,
whitespace, non-decimal text, empty values, and overflow fail validation.
Identical values received through an environment source and command line are
accepted. Conflicting duplicates fail rather than using option order as an
implicit precedence rule.

The first interface describes one conservative circuit envelope. If `--qpu`
names several services, the same envelope applies to each service. A future
interface may add separately identified envelopes without changing the QSGP
header.

The plugin derives walltime from Slurm's job record. It translates finite
minutes into nanoseconds with checked arithmetic. An unlimited time limit is
rejected for a managed QPU request unless site configuration defines a finite
cap.

**Requirements:**
- SLPCLI-001
- SLPCLI-002
- SLPCLI-003
- SLPCLI-004
- SLPCLI-005
- SLPCLI-006
- SLPCLI-007
- SLPCLI-008
- SLPCLI-009
- SLPCLI-011

**Status:** draft

## DES-SLURM-006. SPANK Lifecycle

The plugin uses the following callbacks:

| Callback and context | Behavior |
| --- | --- |
| `slurm_spank_init`, all applicable contexts | Load immutable plugin configuration and register options. No gateway call occurs. |
| Option callback, local or allocator | Parse and validate user values before Slurm accepts the command. |
| Option callback, remote | Reconstruct the same validated option state received from Slurm. |
| `slurm_spank_init_post_opt`, remote | Resolve Slurm metadata, acquire or retrieve reservations through QSGP, and set `QFW_RESERVATIONS`. |
| `slurm_spank_task_init`, remote | Return `SLURM_ERROR` when acquisition failed, preventing task execution without treating a healthy node as failed. |
| `slurm_spank_exit`, local or remote | Free process-local buffers only. It never releases allocation reservations. |
| `slurm_spank_job_epilog`, job-script context | List the canonical job's reservations and issue one release for each tuple. Log failures while returning success to avoid draining the node. |
| `slurm_spank_slurmd_exit`, slurmd context | Release daemon-local configuration and protocol resources only. |

For `sbatch`, remote initialization occurs before the batch task executes. For
`salloc`, no reservation is created merely because an interactive allocation
exists. The first `srun` triggers remote initialization and acquires the QPM
before its task starts. Subsequent steps repeat the same idempotent reserve
keys and receive the existing reservation tuples.

If a later `srun` does not repeat the allocation's option values, the plugin
uses `LIST_RESERVATIONS` for the canonical job and exports the existing tuple
set. A step with neither propagated options nor an existing tuple set is not a
managed quantum step.

The job epilog is an independent process. It therefore queries the gateway
journal rather than relying on globals created during remote initialization.
Multiple epilog invocations caused by multinode execution are safe because
list and release are idempotent.

**Requirements:**
- SLPLIFE-001
- SLPLIFE-002
- SLPLIFE-003
- SLPLIFE-004
- SLPLIFE-008
- SLPFAIL-003

**Status:** draft

## DES-SLURM-007. Reservation Transaction

Remote initialization builds the ordered list of exact service IDs from the
root-owned resource mapping. It then executes this algorithm:

```text
accepted = []
for service_id in requested_services:
    response = qsgp_reserve(job, service_id, workload)
    if response.outcome != ACCEPTED:
        for tuple in reverse(accepted):
            qsgp_release(job, tuple, ROLLBACK)
        defer task-launch failure using response diagnostic
        return
    accepted.append(response.tuple)

QFW_RESERVATIONS = canonical_json(accepted)
spank_setenv(QFW_RESERVATIONS)
```

The plugin, not the gateway, owns ordering and rollback. It attempts every
rollback even after one release fails and reports all failures. The gateway
receives one service operation at a time.

The environment value is a compact JSON list of two-element arrays. Service
IDs are JSON strings. Reservation IDs are decimal strings so their `uint64_t`
range does not lose precision in JSON consumers.

```bash
QFW_RESERVATIONS='[["iqm-ornl-20q","41"],["nwqsim-site","17"]]'
```

The plugin writes the environment through `spank_setenv()` in remote context.
It does not create a reservation file or expose gateway journal paths to the
application.

**Requirements:**
- SLPLIFE-005
- SLPLIFE-006
- SLPLIFE-007
- SLPLIFE-009
- SLPLIFE-010
- SLPADM-007
- SLPADM-008

**Status:** draft

## DES-SLURM-008. Gateway Process Model

`qfw-slurm-gateway` is a persistent service managed by systemd or an
equivalent site service manager. It runs under a dedicated account, has a
locked-down state directory, and can read the QFw site connection record. QPU
provider credentials remain readable only by QPMd.

Startup performs these steps:

1. Validate gateway configuration and protected-path permissions.
2. Open and migrate the SQLite journal under an exclusive schema lock.
3. Activate the configured QFw environment.
4. Initialize one DEFw runtime.
5. Connect to the configured DEFw directory service and verify readiness.
6. Bind the TCP listener only after journal and DEFw initialization succeed.
7. Notify the service manager that the gateway is ready.

The gateway joins DEFw as a client process. It does not advertise itself as a
QPM and does not put its native listener into the DEFw directory. The plugin
learns the gateway endpoint only from root-owned plugin configuration.

The server uses an asynchronous TCP accept loop. Decoding and authentication
remain in the event-loop task. Blocking DEFw calls execute in a bounded worker
pool. Per-reservation-key locks prevent duplicate QPM calls, while different
jobs can progress concurrently.

Shutdown stops accepting connections, waits for bounded in-flight handlers,
flushes the journal, disconnects DEFw, and exits. It does not release active
reservations merely because the gateway service is restarting.

**Requirements:**
- SLPGW-001
- SLPGW-005
- SLPGW-008
- SLPGW-009
- SLPARCH-006

**Status:** draft

## DES-SLURM-009. Directory and QPM Binding

The gateway adapter maintains a resolver bound to the configured directory
service. A reserve operation includes one exact `service_id`. Resolution uses
that identifier and the QPM admission-control API category. Provider,
capability, and device-type searches are not used as fallback selection.

An exact lookup must produce one live registration. No result, multiple
results, an expired generation, a missing admission binding, or failed QPM
readiness returns a structured gateway error. The resolver never substitutes
a service with matching capabilities.

The gateway records the resolved QPM runtime ID and directory generation with
an accepted tuple. A later release resolves the same service ID again. If the
runtime ID or generation changed, the previous QPM reservation is treated as
invalidated by restart. The gateway records a terminal stale-runtime result
and does not send the old reservation ID to the new incarnation.

Directory reconnect uses bounded exponential backoff between requests. A
request does not wait beyond its protocol deadline. Reconnect does not change
the requested service ID or erase journaled tuples.

**Requirements:**
- SLPADM-002
- SLPADM-003
- SLPADM-004
- SLPGW-001
- SLPGW-002
- SLPFAIL-005

**Status:** draft

## DES-SLURM-010. QPM Request Translation

After resolving the exact QPM, the gateway constructs the existing QPM
reservation request. Trusted values and user-declared workload values remain
separate during construction.

| QPM request value | Source |
| --- | --- |
| `owner.user` | Username resolved from the verified Slurm UID |
| `job_id` | Canonical Slurm job ID |
| `allocation_id` | Cluster name and canonical job ID |
| `scope_id` | Site mapping of Slurm account and QoS |
| `target_device_id` | QPM service and site manifest configuration |
| `workload_kind` | Validated plugin option |
| `walltime_ns` | Checked conversion of Slurm time limit |
| `ttl_ns` | Site policy bounded by allocation walltime plus cleanup grace |
| `task_class.count` | Declared circuit count |
| `task_class.qubit_count` | Declared maximum qubits |
| `task_class.depth` | Declared maximum depth |
| `task_class.shots` | Declared maximum shots |
| Gate and measurement counts | Corresponding optional maximum options |

QPMd assigns the reservation ID, checks user and device entitlement, binds
credentials, and calls qhw-admission. The gateway forwards none of the test
driver's credential hints, arbitrary parameter JSON, or user-selected scope
overrides.

The normalized QPM decision maps as follows:

| QPM result | QSGP outcome |
| --- | --- |
| Accepted with nonzero reservation ID | `ACCEPTED` |
| Delayed with retry information | `DELAYED` |
| Rejected by entitlement, credentials, policy, or limits | `REJECTED` |
| RPC, directory, malformed-result, or gateway failure | `ERROR` |

The gateway copies only bounded diagnostics. It never serializes Python
exceptions, tracebacks, credentials, or arbitrary result objects into QSGP.

**Requirements:**
- SLPCLI-010
- SLPADM-005
- SLPADM-006
- SLPADM-007
- SLPGW-003
- SLPGW-004
- SLPGW-009

**Status:** draft

## DES-SLURM-011. QSGP Wire Encoding

The C encoder writes every field explicitly. It does not transmit a native C
structure with `send(fd, &header, sizeof(header), ...)`. This keeps the gateway
portable across supported Linux distributions and host architectures.

The 32-byte fixed header has these offsets:

```text
0x00  magic[4]          "QSGP"
0x04  major_version     uint16, network order
0x06  minor_version     uint16, network order
0x08  message_type      uint16, network order
0x0a  flags             uint16, network order
0x0c  header_size       uint32, network order
0x10  correlation_id    uint64, network order
0x18  payload_size      uint32, network order
0x1c  reserved          uint32, network order, zero
```

Operation codes reserve the high bit for responses:

| Code | Message |
| ---: | --- |
| `0x0001` | `HEALTH_REQUEST` |
| `0x0002` | `RESERVE_REQUEST` |
| `0x0003` | `LIST_RESERVATIONS_REQUEST` |
| `0x0004` | `RELEASE_REQUEST` |
| `0x8001` | `HEALTH_RESPONSE` |
| `0x8002` | `RESERVE_RESPONSE` |
| `0x8003` | `LIST_RESERVATIONS_RESPONSE` |
| `0x8004` | `RELEASE_RESPONSE` |

Each TLV uses this header:

```text
type       uint16, network order
flags      uint16, network order
length     uint32, network order
value      exactly length bytes
padding    zero to the next four-byte boundary
```

Flag bit zero means that the receiver must understand the TLV. Other flag bits
are zero in version 1. Decoders check the remaining payload before every read,
use checked addition for padding, validate fixed numeric lengths, and require
all padding bytes to be zero.

**Requirements:**
- SLPPROTO-001
- SLPPROTO-004
- SLPPROTO-005
- SLPPROTO-006
- SLPPROTO-008
- SLPPROTO-011
- SLPPROTO-012

**Status:** draft

## DES-SLURM-012. QSGP Field Registry

Version 1 assigns the following TLV identifiers:

| ID | Name | Encoding |
| ---: | --- | --- |
| `0x0001` | Cluster name | UTF-8 string |
| `0x0002` | Canonical job ID | `uint64` |
| `0x0003` | Heterogeneous job ID | `uint64` |
| `0x0004` | Heterogeneous component | `uint32` |
| `0x0005` | Job UID | `uint32` |
| `0x0006` | Job GID | `uint32` |
| `0x0007` | Service ID | UTF-8 string |
| `0x0008` | Workload kind | `uint32` enumeration |
| `0x0009` | Walltime nanoseconds | `uint64` |
| `0x000a` | Circuit count | `uint64` |
| `0x000b` | Maximum qubits | `uint32` |
| `0x000c` | Maximum depth | `uint64` |
| `0x000d` | Maximum shots | `uint64` |
| `0x000e` | Maximum one-qubit gates | `uint64` |
| `0x000f` | Maximum two-qubit gates | `uint64` |
| `0x0010` | Maximum measurements | `uint64` |
| `0x0011` | Reservation ID | `uint64` |
| `0x0012` | Release reason | `uint32` enumeration |
| `0x0013` | Outcome | `uint32` enumeration |
| `0x0014` | Reason code | `uint64` |
| `0x0015` | Retry delay nanoseconds | `uint64` |
| `0x0016` | Diagnostic | UTF-8 string, maximum 4096 bytes |
| `0x0017` | QPM runtime ID | UTF-8 string |
| `0x0018` | QPM generation | `uint64` |
| `0x0019` | Reservation state | `uint32` enumeration |
| `0x0020` | Reservation record | Nested TLV container |

`LIST_RESERVATIONS_RESPONSE` repeats reservation-record containers. Each
container carries service ID, reservation ID, runtime ID, generation, and
state. Nested depth is limited to one in version 1.

Outcomes use `1=ACCEPTED`, `2=DELAYED`, `3=REJECTED`, and `4=ERROR`.
Release results use the reservation-state TLV and a reason code to distinguish
released, already terminal, not found, stale runtime, authorization failure,
QPM failure, and gateway failure.

**Requirements:**
- SLPPROTO-004
- SLPPROTO-006
- SLPPROTO-007
- SLPPROTO-009
- SLPPROTO-010
- SLPPROTO-012

**Status:** draft

## DES-SLURM-013. MUNGE and TCP Envelope

The plugin encodes the complete QSGP frame as the payload of a MUNGE
credential. It then connects to the configured gateway and sends:

```text
credential_length  uint32, network order
credential_bytes   credential_length bytes, no trailing nul
```

The gateway reads exactly four bytes, validates the configured size bound,
reads the complete credential, decodes it with MUNGE, validates its age and
origin UID, and parses the decoded QSGP frame. The response follows the same
format and is MUNGE-encoded by the gateway service identity. The plugin
validates that identity before parsing the response.

MUNGE supplies message authentication and confidentiality inside the cluster
trust domain. The gateway also keeps a bounded replay cache keyed by MUNGE
credential identity and QSGP correlation ID until the credential expires. An
identical retry with the same logical reserve key reaches idempotency handling;
a replay that changes the decoded operation or fields is rejected.

The listener accepts only the QSGP protocol. It does not expose HTTP, pickle,
YAML, Python object serialization, or DEFw framing. Requests and responses are
limited to 64 KiB by default.

**Requirements:**
- SLPPROTO-002
- SLPPROTO-003
- SLPSEC-001
- SLPSEC-002
- SLPSEC-003
- SLPPROTO-011

**Status:** draft

## DES-SLURM-014. Synchronous Client Operation

Each plugin operation uses a new nonblocking socket with blocking semantics
implemented through `poll()`. The client applies one absolute deadline across
DNS or address resolution, connect, send, receive, authentication, and decode.

```text
encode QSGP frame
    -> munge_encode(frame)
    -> connect with deadline
    -> write_all(length + credential)
    -> read_exact(response length)
    -> read_exact(response credential)
    -> munge_decode(response)
    -> validate QSGP header and correlation
    -> decode typed response
    -> close socket
```

`write_all()` and `read_exact()` handle `EINTR`, short operations, peer close,
and deadline expiry. They never retry a partial transaction on a new socket.
The operation layer retries the complete request with the same correlation and
idempotency identity when policy permits.

The plugin receives the result directly on the calling stack. No background
thread, callback, file polling, or second query is needed for a successful
request. A timeout after the request may have reached the gateway is reported
as unknown outcome. The retry uses the same cluster, job, and service key, so
the gateway returns the accepted tuple rather than reserving twice.

**Requirements:**
- SLPGW-004
- SLPPROTO-002
- SLPPROTO-008
- SLPFAIL-001
- SLPFAIL-002

**Status:** draft

## DES-SLURM-015. Gateway Journal and Idempotency

SQLite stores one row per allocation and service. The database and its parent
directory are readable only by the gateway account.

```text
reservation_journal
  cluster_name             TEXT
  canonical_job_id         INTEGER-as-decimal-text
  service_id               TEXT
  request_fingerprint      BLOB
  state                    TEXT
  reservation_id           INTEGER-as-decimal-text nullable
  qpm_runtime_id            TEXT nullable
  qpm_generation            INTEGER-as-decimal-text nullable
  outcome                  TEXT
  reason_code              INTEGER-as-decimal-text nullable
  created_at_ns             INTEGER-as-decimal-text
  updated_at_ns             INTEGER-as-decimal-text
  PRIMARY KEY(cluster_name, canonical_job_id, service_id)
```

Unsigned 64-bit values use validated decimal text because SQLite signed
integers do not cover the complete `uint64_t` range.

Reserve processing acquires a per-key lock and starts with a journal lookup.
An accepted row with the same request fingerprint returns its tuple. A
different fingerprint for the same job and service is rejected as a
conflicting duplicate. A pending row is completed by its existing owner or
waited on within the request deadline.

Before calling QPMd, the gateway commits a pending row. After acceptance it
commits the reservation ID, runtime ID, generation, and accepted state before
sending the response. If the accepted update cannot be committed, the gateway
attempts an immediate QPM release and returns an error without acknowledging
the tuple.

Release validates that the tuple belongs to the requesting job. A successful
or already-terminal QPM response advances the journal to a terminal state.
Rows remain for a configurable audit and retry interval. The journal does not
hold capacity or reconstruct QPM state.

**Requirements:**
- SLPARCH-008
- SLPGW-005
- SLPGW-006
- SLPGW-007
- SLPGW-008
- SLPFAIL-002
- SLPFAIL-004

**Status:** draft

## DES-SLURM-016. Job Verification and Authorization Boundary

MUNGE authenticates the process sending QSGP. The gateway accepts lifecycle
requests only from configured privileged identities used by Slurm daemons.
The job UID carried inside the request is still verified against Slurm state.

The initial verifier runs a bounded `scontrol --json show job <job-id>` command
under the gateway service account. It checks:

- The cluster and canonical job exist.
- The job UID equals the QSGP job UID.
- The job is starting, running, completing, or otherwise valid for the
  requested operation.
- The QPU resource name and heterogeneous component agree with allocated job
  data when Slurm exposes them.
- A release refers to the same job identity recorded at reserve time.

The verifier is a narrow interface so a site can replace the command adapter
with Slurm REST or a native Slurm binding. A verifier failure is fail-closed.

QFw v0.1 trusts the verified Slurm identity supplied through this path when
QPMd evaluates user entitlement. Application authentication to later QPM APIs
is a separate design. The gateway never reads `qpu-users.json` and never
handles provider API keys.

**Requirements:**
- SLPSEC-001
- SLPSEC-002
- SLPSEC-003
- SLPSEC-004
- SLPSEC-005
- SLPGW-009

**Status:** draft

## DES-SLURM-017. Release and Failure Recovery

The job epilog sends `LIST_RESERVATIONS` using the canonical allocation key.
It then sends one `RELEASE` for each nonterminal record. A release failure does
not stop later releases. Duplicate epilogs observe terminal rows and complete
without a second QPM state transition.

The epilog logs unresolved releases and returns success. Returning an error
from a required SPANK job-epilog hook can cause Slurm to treat a healthy node
as failed. Capacity safety instead comes from QPM reservation TTL, explicit
operator retry, and the durable gateway journal.

The gateway provides an administrative command that lists nonterminal journal
rows and resubmits explicit release requests. This command invokes the same
release handler as the plugin. The gateway does not run an autonomous reaper
that decides a Slurm job has ended.

Failure mapping follows these rules:

| Failure | Plugin behavior | Journal behavior |
| --- | --- | --- |
| Local option error | Reject command before launch. | No row. |
| Gateway unavailable before reserve | Fail task launch. | No new acknowledged row. |
| Reserve timeout | Retry same idempotent key, then fail with unknown outcome. | Pending or accepted row remains discoverable. |
| QPM delayed or rejected | Roll back earlier services and fail task launch. | Store normalized terminal outcome. |
| QPM restart | Fail old tuple as stale. | Mark stale runtime. |
| Release timeout | Continue releasing other tuples and report failure. | Nonterminal row remains. |
| Gateway restart | Reconnect on retry. | Recover rows from SQLite. |

**Requirements:**
- SLPLIFE-004
- SLPLIFE-006
- SLPLIFE-007
- SLPFAIL-002
- SLPFAIL-003
- SLPFAIL-004
- SLPFAIL-005

**Status:** draft

## DES-SLURM-018. Heterogeneous and Concurrent Allocations

Heterogeneous components can invoke remote initialization independently. The
plugin normalizes each component to the leader's canonical job ID and carries
the component identifier for validation and diagnostics. The gateway's
allocation-and-service uniqueness key prevents the same QPM service from being
reserved twice by two components.

If a component requests a different QPU service, its reservation joins the
same allocation tuple set. Every application step receives the complete set
returned by `LIST_RESERVATIONS`, ordered lexically by service ID for stable
environment generation.

Concurrent jobs use different canonical job IDs and therefore independent
locks and journal rows. The bounded worker pool prevents an unavailable QPM or
directory connection from consuming unlimited gateway threads. Per-key
serialization prevents reserve and release races for one job and service.

**Requirements:**
- SLPLIFE-002
- SLPLIFE-008
- SLPLIFE-009
- SLPGW-005
- SLPVAL-004

**Status:** draft

## DES-SLURM-019. Logging and Operational Status

The plugin uses Slurm logging and includes the plugin version, protocol
version, cluster, job ID, component, service ID, operation, correlation ID,
outcome, reason code, and duration. User-visible errors remain bounded and
actionable.

The gateway emits structured records with the same correlation fields plus
directory generation and journal transition. It records no MUNGE credential,
provider credential, device-access content, user circuit, or QPM result
payload.

The gateway exposes `HEALTH` through QSGP. A successful response reports
protocol compatibility, journal readiness, DEFw initialization, and directory
connectivity. It does not enumerate credentials or reservations. Systemd
readiness is asserted only after the same local checks pass.

An administrative status command reads the journal through a protected local
gateway operation or directly under the gateway account. Application users do
not receive journal access.

**Requirements:**
- SLPPROTO-007
- SLPPROTO-010
- SLPSEC-006
- SLPFAIL-004

**Status:** draft

## DES-SLURM-020. Build, Deployment, and Upgrade

The plugin is built separately for each supported Slurm ABI and installed in
the site's Slurm plugin directory. `plugstack.conf` marks it required and
passes only the root-owned configuration path:

```text
required /usr/lib64/slurm/spank_quantum.so \
    config=/etc/qfw-slurm/plugin.conf
```

The gateway is installed as an independent service. Its unit selects the QFw
installation and virtual environment before executing the gateway module. The
service starts after MUNGE and the site DEFw directory service are available.

Plugin and gateway releases declare the QSGP major and supported minor range.
A rolling upgrade begins with a gateway that accepts both old and new minor
versions, followed by plugin replacement. A major-version mismatch fails
`HEALTH` and reservation without attempting partial interpretation.

The QFw Slurm Docker cluster builds and installs both artifacts, provisions
MUNGE trust and root-owned configuration, starts the gateway, and enables the
plugstack entry. The official QFw installation remains independently
replaceable through its normal prefix and virtual-environment mechanism.

**Requirements:**
- SLPARCH-001
- SLPPROTO-012
- SLPSEC-005
- SLPVAL-008

**Status:** draft

## DES-SLURM-021. Validation Design

The C unit suite invokes option callbacks with valid, missing, repeated,
conflicting, zero, boundary, overflow, and malformed values. Protocol tests
share golden vectors between the C encoder and Python decoder. Fuzz targets
exercise the frame and TLV decoders with authentication disabled only inside
the test harness.

Gateway tests provide fake directory and QPM bindings. They cover accepted,
delayed, rejected, malformed, timeout, generation-change, concurrent reserve,
partial rollback support, journal-write failure, restart, list, and release
paths.

The Docker Slurm suite runs these scenarios as `user-a`, `user-b`, and
`user-c`:

1. `sbatch` acquires one NWQSim reservation, exports it, runs a QFw example,
   and releases it in the epilog.
2. `salloc` runs two sequential `srun` steps against one reservation and proves
   the reservation remains active between steps.
3. A heterogeneous allocation obtains the same tuple set in each application
   component without duplicate reservation.
4. A two-service request forces the second reserve to fail and confirms that
   the plugin releases the first.
5. Concurrent users reserve the same long-running QPM under separate job IDs
   without seeing each other's journal state.
6. Gateway restart between reserve and release preserves explicit cleanup.
7. NWQSim and fakeIQM pass before a bounded real-IQM chemistry job is enabled.

Tests also verify filesystem permissions, log redaction, invalid MUNGE
credentials, replay handling, response-correlation mismatches, and gateway
timeouts.

**Requirements:**
- SLPVAL-001
- SLPVAL-002
- SLPVAL-003
- SLPVAL-004
- SLPVAL-005
- SLPVAL-006
- SLPVAL-007
- SLPVAL-008

**Status:** draft

## DES-SLURM-022. Requirements Traceability

| Requirement group | Design sections |
| --- | --- |
| `SLPARCH-*` | DES-SLURM-001, DES-SLURM-002, DES-SLURM-004, DES-SLURM-008, DES-SLURM-020 |
| `SLPCLI-*` | DES-SLURM-005, DES-SLURM-010 |
| `SLPADM-*` | DES-SLURM-003, DES-SLURM-007, DES-SLURM-009, DES-SLURM-010 |
| `SLPLIFE-*` | DES-SLURM-004, DES-SLURM-006, DES-SLURM-007, DES-SLURM-017, DES-SLURM-018 |
| `SLPPROTO-*` | DES-SLURM-011, DES-SLURM-012, DES-SLURM-013, DES-SLURM-014, DES-SLURM-019, DES-SLURM-020 |
| `SLPGW-*` | DES-SLURM-008, DES-SLURM-009, DES-SLURM-010, DES-SLURM-014, DES-SLURM-015 |
| `SLPSEC-*` | DES-SLURM-003, DES-SLURM-013, DES-SLURM-016, DES-SLURM-019, DES-SLURM-020 |
| `SLPFAIL-*` | DES-SLURM-014, DES-SLURM-015, DES-SLURM-017 |
| `SLPVAL-*` | DES-SLURM-002, DES-SLURM-018, DES-SLURM-020, DES-SLURM-021 |

Every implementation unit and test plan derived from this design should cite
the individual requirement IDs it covers. Group traceability in this section
is an index rather than a replacement for record-level links.

**Status:** draft
