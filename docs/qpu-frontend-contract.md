# QPU Front-End Contract — Design Proposal

Status: **Draft — revised after vendor feedback**
Author: Doug Oucharek
Date: 2026-05-27 (revised 2026-06-08)

## 1. Purpose

QFw submits quantum jobs to QPU resources that may be reached through different
lower-level interface libraries — today **QRMI** (IBM's Rust + C + Python
resource-management interface) and **QDMI** (the Munich/MQSS C device interface),
and tomorrow other vendor-specific libraries. SLURM integration is a separate **SPANK-plugin layer**
that wraps whichever interface drives a resource — not built into either
(Section 11).

This document proposes a **common front-end** that sits between QFw's QPM
service layer and those libraries. The front-end is a **contract between users
of QPU resources and the vendors that supply them**:

- Any capability a user needs is added to the front-end.
- Vendors then implement that capability in their chosen library (QRMI, QDMI,
  or other), or leave it unimplemented (NULL).

### Goals

- **Upper layers never change.** SLURM and the SPANK plugins must not be
  modified as new QPU resources or vendor libraries are added. All
  interface variance is absorbed by the front-end through capability
  negotiation.
- **Union, not intersection.** The contract exposes the *superset* of calls
  that make sense across libraries, not the lowest common denominator.
  Unimplemented calls are NULL-ed out per resource.
- **A path to a standard — not another permanent layer.** The front-end is an
  *implementation-first* contract (the libfabric/OFI model): a working reference
  that evolves through real use and can later be codified into an open
  *specification* each vendor implements directly (the MPI model), at which point
  the shim retires. The capability map shows where the API must grow; it is
  **not** a lever to force vendors' codebases to merge. See Section 14.

### Non-goals

- Replacing QRMI or QDMI. The front-end orchestrates them; it does not
  reimplement them.
- Hardware-faithful performance modeling (that remains out of scope for the
  QFw-SLURM-Cluster environment generally).

## 2. Core idea: a union contract with capability negotiation

Most abstraction layers expose only what every backend supports (the
intersection). This proposal does the opposite: it exposes the **union** and
makes **capability negotiation a first-class, mandatory part of the contract**
so that callers can ask "does this resource support X?" *before* calling X.

The contract therefore has three co-equal halves:

1. **Capability descriptor** — what a given resource/library actually
   implements.
2. **Call surface** — the union of function signatures.
3. **Data schemas** — the semantics of payloads. QFw already normalizes
   provider-native output to a common **`qhw` schema** (today via the
   `qhw-iqm` / `qhw-data` submodules). NULL-able functions handle *presence*;
   the qhw schema handles *meaning*. Both are required or the contract drifts.

Adding a capability to the contract is a **spec change** (signature + semantics
+ qhw schema), not merely a code change. That friction is deliberate: it keeps
the union meaningful and prevents feature sprawl.

## 3. Faceting

SLURM and SPANK do not care about circuits; they care about resource lifecycle
and accounting. Applications care about device topology, calibration, and
circuit execution. These concerns map onto the two libraries' respective
strengths, so the contract is split into **facets**, with capability
negotiation and a concurrency rule **per facet**.

| Facet | Consumed by | QRMI | QDMI | Concurrency rule | Routing rule |
|---|---|---|---|---|---|
| Resource Management | SLURM + SPANK | rich (acquire/release) | session-based | single session | session owner |
| Device Introspection | applications | thin | rich | freely composable | per-call; overlaps by preference |
| Execution | applications | provider-defined | rich | single library, whole transaction | bound to session owner |

The QRMI/QDMI columns here are **representative**; because each library is itself
a multi-provider layer, the real capabilities are **per resource** (Section 5.1).
SLURM scheduling is **not** a column above — it is a SPANK-plugin layer that
wraps either interface (Section 11), orthogonal to the QRMI-vs-QDMI choice.

Faceting is what actually delivers the "upper layers never change" guarantee:
SPANK binds only to the narrow, stable Resource-Management facet, while the
wide, fast-moving Execution/Introspection surface never touches the scheduler.

## 4. Concurrent composition

The front-end may drive **more than one library concurrently for the same
resource**, routing each contract call to whichever library implements it. This
minimizes NULL-outs and yields the gap map (Section 9). The granularity of
"concurrent," however, differs by facet.

### Safe to compose: read-only / idempotent calls (Introspection)

Topology, coupling, calibration, operations, and quality metrics are read-only
and idempotent. Even where a library keeps a session with the device (QDMI
always does — it is **not** stateless), a read does not mutate execution state,
so:

- Non-overlapping calls route to whichever library has them.
- Overlapping calls (both expose calibration) are resolved by a **user
  preference** (environment variable, see Section 6).

### Must stay within one library: stateful execution (Execution + acquisition)

Execution calls are a transaction and share backend state:

- **Job handles are not portable.** A job submitted via QRMI (`task_start`)
  must be polled and fetched via QRMI (`task_result`); QDMI's job handle lives
  in a different ID space. They do not interoperate today.
- **Acquisition binds execution.** QRMI's `acquire`/`release` reserves the QPU,
  and the SPANK plugin (Section 11) ties that reservation to the SLURM
  allocation. If the reservation holder is one library but the circuit is
  submitted through a separate session in another, the work runs *out-of-band*
  of the reservation and the scheduling/accounting guarantee silently breaks.

Therefore the `acquire → submit → status → result → release` sequence is **one
transaction in one library**, and that library is dictated by **whoever owns the
reservation** (`session_owner`), not by per-call preference. If no library
implements `acquire` (a pure device with no reservation concept), the binding is
free and falls through to preference.

### Cost control: lazy second library

Running two libraries means two auth contexts, two connections, and two failure
modes. The second library is therefore **instantiated lazily** — only when the
primary NULLs a call the user actually requested — so the common single-library
case stays cheap.

## 5. The capability descriptor

Every resource carries a descriptor. Upper layers and applications read the
descriptor instead of knowing which vendor library is underneath.

```yaml
resource:
  id: ornl-iqm-20q
  provider: iqm
  contract_version: 1
  libraries: [qrmi, qdmi]          # which are wired for this resource
  preference: qdmi                 # tiebreaker for composable overlaps (env-overridable)
  facets:
    resource_mgmt:
      session_owner: qrmi          # owner of acquire() — binds execution
      caps: { is_accessible: qrmi, acquire: qrmi, release: qrmi,
              status: [qrmi, qdmi], accounting: qrmi, target_meta: [qrmi, qdmi] }
    introspection:
      caps: { architecture: qdmi, coupling: qdmi, calibration: [qdmi, qrmi],
              operations: qdmi, quality_metrics: NULL }
    execution:
      bound_to: resource_mgmt.session_owner   # = qrmi here
      caps: { submit: qrmi, status: qrmi, result: qrmi, cancel: qrmi, wait: qrmi }
```

`NULL` means no wired library implements the capability — a gap-map entry.
A list (e.g. `[qdmi, qrmi]`) means composable; preference breaks the tie.

### 5.1 Capabilities are per resource — libfabric-style providers

QRMI and QDMI are not monolithic; each is **itself a multi-provider layer**.
Multiple QPU resources plug in below QRMI (IBM Quantum, Pasqal, …) — exactly as
providers plug into **libfabric** — and QDMI has per-device drivers below it
(e.g. QDMI-on-IQM). A capability therefore belongs to the **resource-plus-library
leaf**, not to "QRMI" or "QDMI" as a whole: QRMI's coverage for an IBM Heron
differs from a Pasqal device, and QRMI has no IQM provider today (so IQM is
reached via QDMI / QDMI-on-IQM).

Consequence: **there is one descriptor and one capability map per resource.** The
per-library columns shown in Sections 3, 6, and 9 are *illustrative*; the
authoritative capabilities are always read from the resource's own descriptor.

## 6. Call surfaces by facet

### Facet A — Resource Management (single session; routed to session owner)

| Contract call | QRMI | QDMI | NULL-out behavior |
|---|---|---|---|
| `is_accessible()` | yes | partial (status) | sentinel `NOT_IMPLEMENTED` |
| `acquire() -> lease` | yes | no | NULL -> no-op, returns ambient handle |
| `release(lease)` | yes | no | NULL -> no-op |
| `status()` | yes | yes | composable (read-only) |
| `accounting()` | yes | no | NULL |
| `target_meta()` | yes | yes | composable |

`acquire()` sets the binding for the Execution facet.

### Facet B — Device Introspection (freely composable; per-call, overlaps by preference)

| Contract call | QRMI | QDMI |
|---|---|---|
| `architecture()` (sites/qubits) | partial (target meta) | rich |
| `coupling()` | partial | yes |
| `calibration()` | partial | yes |
| `operations()` (native gates) | partial | yes |
| `quality_metrics()` | no | no (NULL today — gap) |

This is where the preference environment variable applies.

### Facet C — Execution (single library, whole transaction; bound to session owner)

| Contract call | QRMI | QDMI |
|---|---|---|
| `submit(circuit, shots, opts) -> job` | `task_start` | submit |
| `status(job)` | `task_status` | status |
| `result(job) -> qhw_result` | `task_result` | get_result |
| `cancel(job)` | `task_stop` | cancel |
| `wait(job, timeout)` | poll | poll |

The whole sequence runs in one library; job handles do not cross libraries.

## 7. Uniform return semantics

Every contract call returns one of a fixed, vendor-neutral set — never a
vendor-specific error. This is what keeps the upper layers stable.

- `OK(payload)` — payload always in the **qhw schema**, regardless of source
  library.
- `NOT_IMPLEMENTED` — no wired library covers this capability (the gap-map
  signal).
- `NOT_ACQUIRED` — an execution call arrived before `acquire()` on a resource
  whose RM facet requires a lease.
- `UNSUPPORTED_INPUT` — a circuit form the bound library cannot accept and the
  front-end cannot transcode.

SLURM/SPANK only ever branch on these states; they never see `iqm` vs `qrmi`
vs `qdmi`.

## 8. Circuit-in / result-out normalization

- **In:** the contract carries a canonical circuit form; the descriptor
  declares what each library ingests; the front-end transcodes. (This
  generalizes today's QASM -> `iqm.pulse` step in
  `services/svc_iqm_qpm/util_iqm.py`.)
- **Out:** every `result()` is normalized to the existing **qhw schema** via a
  per-library normalizer (`qhw-qdmi`, `qhw-qrmi`) modeled on today's
  `qhw-iqm`. This invariant keeps the `backends/qfw_qiskit` primitives and the
  statevector contract untouched.

## 9. The gap map

Inverting the `caps` matrix across resources produces a vendor scorecard:

```
capability         qrmi   qdmi
acquire/release      x      -    <- QDMI gap: no reservation lifecycle
accounting           x      -    <- QDMI gap
coupling/calib       ~      x    <- QRMI gap: thin device introspection
quality_metrics      -      -    <- neither: user-driven future contract growth
```

Because it is generated from conformance declarations rather than maintained by
hand, the gap map shows **where the API must grow** and, ultimately, **what an
eventual open specification should standardize** (Section 14). It is computed
**per resource** (Section 5.1): the columns above are illustrative, and the same
library can show a different profile depending on which QPU is plugged in below
it. The map informs the specification; it does not force vendors' codebases
together.

## 10. Where this lands in QFw

The shim is a **new, separate QPM service that runs in parallel** to the native
IQM service — *not* an in-place modification of `svc_iqm_qpm`. The native path
is deliberately left untouched so it remains available as a reference
implementation to evaluate the shim against.

- **`service-apis/api_qpm`** is left as-is to start; it is the contract both
  services implement, and is fine-tuned later as the shim matures.
- **`services/svc_iqm_qpm/` is unchanged** — `svc_qrc.py` keeps hard-
  instantiating `IQMServiceClient()`. It is the native, single-vendor
  implementation, kept in parallel for evaluation.
- **New service `services/svc_lib_qpm/`** (the bifurcation layer) implements the
  same `api_qpm` surface. Internally it owns the routing decision — call QRMI or
  QDMI per the criteria in Sections 4–6 (capability, then preference, with
  execution pinned to the reservation owner). It is registered as a peer in
  `setup/qfw_services.yaml` (`shim-ornl-20q`), selectable instead of the native
  `iqm-ornl-20q`.
  - `frontend.py` — the `Frontend` router + `NotImplementedByLibrary` (the
    NOT_IMPLEMENTED / gap-map signal) + `capability_map()`.
  - `svc_qrc.py` — its own run-queue (mirrors the native one) that drives the
    `Frontend` instead of `IQMServiceClient`.
  - `drivers/` — `QrmiDriver`, `QdmiDriver`, each declaring its `CAPABILITIES`
    (the subset of the contract it covers). The lower-level libraries are
    imported lazily, so the service constructs and routes even before a real
    library call is made.
- **`services/util/device_access.py`** config (`dev-config/config.yaml`) gains
  `libraries:`, `preference:`, and per-facet caps.
- **Result normalizers** `qhw-qdmi` / `qhw-qrmi` are added alongside the
  existing `qhw-iqm` to keep the qhw schema stable.
- The second library is instantiated lazily.

The de-facto driver contract already exists implicitly as the public method set
of `IQMServiceClient` (`get_device_info`, `get_backend_info`,
`get_dynamic_backend_info`, `get_coupling_graph`, `get_calibration_snapshot`,
`run_circuit`, `get_last_job_timing`, `get_last_job_metadata`). The shim drivers
declare which of these calls each library covers; promoting the set to an
explicit `QFwDeviceDriver` protocol is a later refinement.

## 11. SLURM / SPANK fit

The QFw-SLURM-Cluster already models QPUs as GRES on the quantum partition:
nodes `c5`–`c8` carry `Gres=qpu:N Features=iqm,superconducting,q20` in
`slurm.conf`. A **SPANK plugin** maps a reservation onto this GRES model and is
the contract surface SLURM binds to (the narrow, stable Resource-Management
facet).

Crucially, that SPANK plugin is a **separate layer that wraps whichever
interface drives the resource** — it is *not* built into QRMI or QDMI. QRMI
ships a SPANK plugin today; a QDMI one is emerging (e.g. PSNC's SLURM HPC plugin
for an AQT system via QDMI, `gitlab.pcss.pl/quantum/psnc-sdk/slurm-hpc-plugin`).
So SLURM scheduling is **orthogonal to the QRMI-vs-QDMI choice** — it sits above
both, alongside the shim, and the shim never makes scheduling a per-library
concern.

## 12. Open decisions

1. **Canonical circuit form** — OpenQASM3 vs QIR. Affects every driver's
   transcoder.
2. **Capability discovery** — static (declared in config) vs dynamic (queried
   from the library at bind time). QDMI can self-report; QRMI metadata is more
   static. The descriptor must pick a model or absorb both.
3. **Preference scope** — a single global `QFW_QPU_IFACE_PREF`, or per-facet
   (e.g. allow preference for Introspection but never for Execution, which is
   reservation-bound regardless).
4. **Contract definition language** — a language-neutral spec (IDL + schema)
   from day one, versus a Python-first prototype with the spec extracted later.
   Only a language-neutral definition can serve as the open specification that
   QRMI (Rust + C + Python) and QDMI (C) each implement against (Section 14).

## 13. Suggested first milestone

Implement **Device Introspection only** through both shims —
`get_device_info` / `coupling()` — before touching execution. This exercises
the factory, the descriptor, capability negotiation, and qhw normalization
without the job-lifecycle and reservation-binding complexity.

## 14. Strategic positioning: implementation-first to a specification

Vendors are wary of "yet another interface." The two ways to standardize a
multi-vendor API frame the choice:

- **Specification-first (the MPI model)** — agree a spec up front; each vendor
  owns and ships its own implementation. Vendors keep control, but it is hard to
  specify an API nobody has battle-tested, and design-by-committee is slow.
- **Implementation-first (the libfabric / OFI model)** — ship one working
  implementation; through real use it becomes the de-facto contract, and a
  standard once adoption grows.

This proposal takes the **implementation-first** path *as a route to* the
specification the vendors want, reconciling both camps:

1. **Implement (now).** The front-end (this contract) lives in the QFw repo as a
   working implementation. We drive QRMI + QDMI on IQM, experiment, and evolve
   the API against real workloads. The capability map (Section 9) is grown from
   real conformance.
2. **Converge.** As the API proves out, the implementation becomes the de-facto
   contract and QRMI/QDMI can **align their APIs** to it. (Convergence here means
   the *APIs* align on a shared surface — each vendor keeps its own
   implementation; this is **not** a codebase merger.)
3. **Specify.** Codify the proven API as an **open specification**. Vendors
   implement the spec directly — each owning its build, MPI-style — and the
   **shim retires**: it was scaffolding to find the right contract, not a
   permanent layer.

The end state is exactly what the vendors asked for — a specification they each
implement — reached via a working reference that de-risks it. The QFw shim is a
**means, not an end.**
