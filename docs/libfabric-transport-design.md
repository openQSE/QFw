# Libfabric Transport Design

## Table Of Contents

- [Purpose](#purpose)
- [Current Transport](#current-transport)
- [Existing Groundwork](#existing-groundwork)
- [Goals And Non-Goals](#goals-and-non-goals)
- [Key Design Decisions](#key-design-decisions)
- [Transport Abstraction](#transport-abstraction)
- [Libfabric Mapping](#libfabric-mapping)
- [Bootstrap And Address Exchange](#bootstrap-and-address-exchange)
- [Large Payloads And RDMA](#large-payloads-and-rdma)
- [Configuration](#configuration)
- [Build Integration](#build-integration)
- [Phased Implementation Plan](#phased-implementation-plan)
- [Testing Strategy](#testing-strategy)
- [Risks And Open Questions](#risks-and-open-questions)

## Purpose

This document describes how QFw adds [libfabric](https://ofiwg.github.io/libfabric/)
(OFI) as a communication transport between DEFw clients and services. The
objectives are:

1. Run the DEFw RPC data path over HPC fabrics such as HPE Slingshot
   (libfabric `cxi` provider) instead of plain TCP sockets.
2. Provide a path to RDMA transfers for very large payloads (for example
   statevectors and large result batches) without routing them through the
   YAML RPC encoding.

The design keeps the Python service and API layers unchanged. Libfabric is
introduced inside the DEFw C layer behind a transport abstraction, so every
QFw service (QPM, QRC, DEFw-resmgr, the QRMI/QDMI shim) inherits the new
transport without modification.

## Current Transport

All client/server communication flows through the DEFw C layer
(`DEFw/src/`):

- Each agent pair holds two TCP sockets: a CTRL channel used for session
  info and heartbeats, and an RPC channel used for Python request/response
  traffic (`defw_listener.c`, `libdefw_agent.c`).
- A single listener thread multiplexes all sockets with `select()`
  (`defw_listener_main()`).
- Every message is framed by `defw_message_hdr_t` (`defw_message.h`):
  message type, payload length, sender IPv4 address, and protocol version.
  The receive path validates the sender by comparing the header IP address
  against the connection peer address.
- RPC payloads are YAML strings. Python serializes the RPC dictionary with
  `yaml.dump()` (`defw_workers.py`) and calls the SWIG-wrapped
  `defw_send_req()` / `defw_send_rsp()` (`libdefw_agent.c`). Received
  messages are dispatched through `msg_process_tbl` and handed back to
  Python via `python_handle_request()` / `python_handle_response()`
  (`defw_python.c`).
- Agents are identified by UUID (`remote_uuid`, `blk_uuid`). The resource
  manager is the rendezvous point that all agents register with.

Two properties of this design matter for the new transport:

- The Python-facing API is already transport-agnostic: send a byte string
  to an agent UUID, receive byte strings tagged with the sender UUID.
- The `select()` loop and the header IP identity check are the only pieces
  that assume TCP sockets specifically.

## Existing Groundwork

Several pieces of libfabric groundwork already exist in the tree:

- The QFw-SLURM-Cluster container image builds libfabric v2.3.1 into
  `/opt/qfw/libfabric`, and `libfabric-install` is a key in the sample
  install configurations.
- `environment/qfw_libfabric_env.sh` exports the libfabric install paths
  and Slingshot-oriented tuning (`FI_LNX_PROV_LINKS="shm+cxi:cxi0|..."`,
  `FI_CXI_RDZV_*`, `FI_MR_CACHE_*`, XPMEM).
- `DEFw/python/experiments/suite_libfabric/` contains an early experiment
  that uses DEFw-resmgr as an out-of-band rendezvous to exchange endpoint
  contexts between agents, which is the bootstrap pattern libfabric RDM
  endpoints require.
- `DEFw/defw_build.yaml` carries a stale `swigify` entry that wrapped a
  private libfabric source tree directly into Python. That experiment
  informs this design (see below) but the entry itself references
  filesystem paths that no longer exist and will be removed.

## Goals And Non-Goals

Goals:

- Move the DEFw RPC data path onto libfabric so QFw can use Slingshot
  (`cxi`), InfiniBand (`verbs;ofi_rxm`), intra-node shared memory (`shm`),
  and linked multi-provider configurations (`lnx`).
- Keep TCP fully working as the default transport and as a runtime
  fallback when no usable OFI provider exists.
- Keep the Python infrastructure (`defw_workers.py`, `defw_agent.py`,
  `defw_remote.py`) and all service code unchanged.
- Provide an explicit RDMA path for large binary payloads that bypasses
  YAML serialization.

Non-goals (for this effort):

- Replacing the TCP control plane (session establishment, heartbeats).
  Liveness detection and bootstrap stay on TCP; see
  [Bootstrap And Address Exchange](#bootstrap-and-address-exchange).
- Changing the RPC encoding for control messages. YAML remains the RPC
  payload format; only large binary attachments move to a binary path.
- Wire-protocol compatibility between old and new builds. The message
  header changes once (IP identity replaced by UUID identity) and
  `DEFW_VERSION_NUMBER` is bumped.

## Key Design Decisions

### Integrate at the C layer, not in Python

An earlier experiment SWIG-wrapped the libfabric headers so Python could
drive `fi_*` calls directly. This design intentionally does not continue
that approach for the production path. Libfabric needs a hot completion
queue progress loop, pinned and registered buffers, and careful
threading. Driving that from Python fights the GIL and Python object
lifetimes, and would require every caller to learn a second API. Placing
libfabric inside the existing C layer behind a transport abstraction
means `defw_send_req()` / `defw_send_rsp()` keep their exact signatures
and all Python code works unchanged.

### Keep TCP for control, use OFI for data

Libfabric reliable-datagram endpoints are connectionless. They provide no
peer-death notification, and they require out-of-band exchange of
endpoint addresses before communication can start. Rather than building
new discovery and liveness machinery on the fabric, the existing TCP CTRL
channel remains the bootstrap and control plane:

- Session-info exchange (already the first message on every connection)
  carries the OFI endpoint address.
- Heartbeats stay on TCP, so agent-death detection keeps working exactly
  as today.
- If either side has no usable OFI provider, the pair silently falls back
  to TCP for RPC traffic as well. Mixed clusters and development
  containers keep working.

### Reliable datagram endpoints with tagged messaging

One `FI_EP_RDM` endpoint per DEFw process, with peers addressed through
an address vector, replaces per-peer sockets. Tagged send/receive
(`fi_tsend`/`fi_trecv`) with the channel and message type encoded in the
tag replaces the two-socket CTRL/RPC split for fabric traffic. This
matches how the relevant providers (`cxi`, `verbs;ofi_rxm`, `shm`, `lnx`,
`tcp`) are designed to be used and avoids connection-management state.

## Transport Abstraction

```
Python (unchanged)            defw_workers / defw_agent / defw_remote
   |  SWIG (unchanged)
   v
libdefw_agent.c  -- agent management, UUIDs, HB policy (transport-agnostic)
   |
   v
defw_transport.h -- vtable: init / connect / send / progress / disconnect
   |-- defw_transport_tcp.c   (today's code, refactored; default)
   `-- defw_transport_ofi.c   (new: libfabric)
```

A new `defw_transport.h` defines the operations table:

```c
typedef enum {
        EN_DEFW_CHANNEL_CTRL = 0,
        EN_DEFW_CHANNEL_RPC,
} defw_channel_t;

typedef struct defw_transport_ops_s {
        /* fabric/domain/endpoint setup and listen resources */
        defw_rc_t (*init)(defw_listener_info_t *info);
        /* resolve peer bootstrap info into a transport handle */
        defw_rc_t (*connect)(defw_agent_blk_t *agent);
        defw_rc_t (*send)(defw_agent_blk_t *agent, defw_channel_t ch,
                          const char *buf, size_t len,
                          defw_msg_type_t type);
        /* one iteration of the event loop; dispatches received
         * messages through msg_process_tbl */
        defw_rc_t (*progress)(void);
        void      (*disconnect)(defw_agent_blk_t *agent);
        void      (*fini)(void);
} defw_transport_ops_t;
```

Supporting changes:

- `defw_agent_blk_t` gains a transport-opaque connection handle: a union
  of the TCP fds (`iFileDesc`, `iRpcFd`) and the OFI peer address
  (`fi_addr_t`) plus per-peer transport state.
- The listener thread body becomes `while (!shutdown) transport->progress();`.
  For TCP, `progress()` is the existing `select()` + accept + dispatch
  logic. For OFI it is a completion-queue read + dispatch.
- Message dispatch (`msg_process_tbl`, `process_msg_py_request()`, and
  friends) is shared by both transports and does not change.
- Header identity check: `defw_message_hdr_t` drops the `struct in_addr`
  sender field and carries the sender `remote_uuid` instead. The receive
  path validates the UUID against the agent block, which works on any
  transport. `DEFW_VERSION_NUMBER` is bumped; old and new builds refuse
  to interoperate, which matches how version mismatches are handled
  today.

## Libfabric Mapping

- Endpoint type: `FI_EP_RDM` (reliable, unconnected, datagram-oriented).
  Capabilities requested in `fi_getinfo()` hints: `FI_MSG | FI_TAGGED`
  initially, plus `FI_RMA | FI_READ | FI_REMOTE_READ` when the RDMA
  attachment path lands (phase 3).
- Addressing: `FI_AV_MAP` address vector. Each peer's `fi_getname()` blob
  is exchanged over the TCP session handshake and inserted with
  `fi_av_insert()`; the resulting `fi_addr_t` is stored in the agent
  block.
- Tag layout (64 bits): `[channel:8][msg_type:8][reserved:48]`. Receives
  are posted with wildcard tags and demultiplexed on completion; the tag
  replaces the "which socket did this arrive on" information.
- Message framing: the (revised) `defw_message_hdr_t` is sent as a prefix
  in the same buffer as the payload, one `fi_tsend()` per message. RDM
  messages are atomic units, so the header/body split reads that the TCP
  code needs do not apply.
- Receive buffers: a pool of pre-registered eager buffers posted with
  `FI_MULTI_RECV`. Messages larger than the eager size are handled by the
  provider's internal rendezvous protocol (on `cxi` this is governed by
  the `FI_CXI_RDZV_*` variables already set in
  `environment/qfw_libfabric_env.sh`), so moderately large YAML RPCs work
  before any explicit RMA support exists.
- Progress: the transport requests `FI_PROGRESS_AUTO` data progress and
  falls back to manual progress driven by the progress thread. The
  progress thread blocks in `fi_cq_sread()` with a timeout equal to the
  heartbeat interval so the existing HB bookkeeping in the listener loop
  keeps its cadence.
- Threading: `fi_tsend()` is called from Python worker threads while the
  progress thread polls the CQ. The domain is opened with
  `FI_THREAD_SAFE`. If profiling later shows lock contention, sends can
  be funneled through the progress thread; that is an optimization, not a
  correctness requirement.
- Completion handling: send completions are counted (with an error path
  that marks the peer dead, mirroring `EN_DEFW_RC_SOCKET_FAIL` handling
  in `defw_send()` today); receive completions carry the buffer to the
  shared dispatch table.

A side benefit: the current `select()` loop is limited by `FD_SETSIZE`
(1024 descriptors, two per peer). The OFI path has no equivalent
per-peer descriptor cost, which matters once QPMs fan out across many
nodes.

## Bootstrap And Address Exchange

Sequence for two agents A (active/connecting) and B (passive/listening),
both OFI-capable:

1. A opens the TCP CTRL connection to B exactly as today and sends
   `defw_msg_session_t`, extended with a new field: the serialized OFI
   endpoint address (`fi_getname()` output, bounded-size byte array) or
   an empty address if A has no OFI endpoint.
2. B inserts A's address into its AV, stores the `fi_addr_t` in A's agent
   block, and returns its own session info (existing behavior) including
   its OFI address.
3. A inserts B's address into its AV. Both sides now mark the peer
   `DEFW_AGENT_OFI_CONNECTED`.
4. RPC traffic (`EN_MSG_TYPE_PY_REQUEST` / `PY_RESPONSE` / `PY_EVENT`)
   for that peer is sent with `fi_tsend()` from then on. Heartbeats and
   session messages stay on the TCP CTRL socket.
5. If either side advertised an empty OFI address, the pair keeps using
   the TCP RPC channel (today's second socket). This is the fallback and
   also the behavior of a TCP-only build.

Notes:

- The separate TCP RPC socket is only established when the pair operates
  in fallback mode, so the fabric path reduces socket count per peer from
  two to one.
- DEFw-resmgr needs no changes: it already brokers agent discovery, and
  the OFI address rides inside the existing per-pair handshake, not
  through the resmgr. This is deliberately simpler than the
  `suite_libfabric` experiment, which brokered contexts through the
  resmgr; the per-pair handshake works even for direct client-to-service
  connections that bypass the resmgr.

## Large Payloads And RDMA

Two separate problems have to be solved for very large data:

1. Serialization. Today a large result is YAML-encoded in Python, copied
   into a C string, sent, and parsed back into Python objects. RDMA
   underneath that encoding would gain little. The design therefore adds
   a binary attachment mechanism to the RPC layer: an RPC can carry, in
   addition to its YAML body, a list of binary buffers. On the Python
   side any object supporting the buffer protocol (NumPy arrays, bytes,
   bytearray) can be attached; the receiving side gets them back as
   buffers alongside the decoded YAML message.
2. Transfer. For the OFI transport, attachments use an explicit
   RMA-read rendezvous:
   - The sender registers the buffer (`fi_mr_reg`) and places a
     descriptor `{rkey, base address, length}` for each attachment into
     the message header extension.
   - The receiver allocates and registers a destination buffer, pulls
     the data with `fi_read()` (RDMA GET), and sends a short completion
     ack (a new message type) so the sender can deregister and release
     the buffer.
   - A memory-registration cache keeps repeated registration of the same
     buffers cheap; `FI_MR_CACHE_*` tuning already exists in
     `environment/qfw_libfabric_env.sh`.

On the TCP fallback path the same attachment API simply streams the
buffers over the socket after the YAML body, preserving one API for both
transports.

The attachment API is additive: `send_req()` and the worker plumbing gain
an optional attachments argument, and existing call sites are untouched.
Service APIs that move bulk data (for example statevector retrieval in
the QPM/QRC path) can adopt attachments incrementally.

## Configuration

- Install configuration (`setup/config/*.yaml`): the existing
  `libfabric-install` key locates the libfabric installation.
- Service/DEFw runtime configuration gains:
  - `transport: tcp | ofi` (default `tcp`). With `ofi`, agents attempt
    OFI and fall back to TCP per peer as described above.
  - `ofi-provider:` optional provider constraint (`tcp`, `shm`, `cxi`,
    `lnx`, `verbs`); empty means take `fi_getinfo()`'s best match.
- Environment overrides, following the existing pattern of `DEFW_*`
  variables: `DEFW_TRANSPORT`, `DEFW_OFI_PROVIDER`. The standard
  libfabric `FI_PROVIDER` filter also works, since provider selection
  flows through `fi_getinfo()`.
- Slingshot tuning stays where it is today, in
  `environment/qfw_libfabric_env.sh` (`FI_LNX_PROV_LINKS`,
  `FI_CXI_RDZV_*`, and related variables).

## Build Integration

- SCons: `defw_transport_ofi.c` compiles only when libfabric is found
  (via `LIBFABRIC_DIR` / `PKG_CONFIG_PATH`, both already exported by
  `qfw_libfabric_env.sh`). Without libfabric, DEFw builds TCP-only, so
  the new transport adds no hard dependency. Minimum supported libfabric
  is 1.20; the container ships 2.3.1.
- A TCP-only build and an OFI-enabled build are wire-compatible with
  each other (the OFI-enabled build simply advertises an OFI address
  when it has one, and a TCP-only peer advertises none).
- The stale `swigify` externals entry in `defw_build.yaml` (absolute
  paths into a private libfabric tree) is removed.
- No SWIG changes: the Python/C interface is unchanged.

## Phased Implementation Plan

Each phase is independently mergeable, and phases 1-3 are opt-in behind
the `transport` configuration knob.

- Phase 0 - transport abstraction (no behavior change):
  - Introduce `defw_transport.h` and move the existing TCP code behind
    it (`defw_transport_tcp.c`).
  - Replace the header IP identity check with the UUID identity check;
    bump `DEFW_VERSION_NUMBER`.
  - Exit criteria: existing smoke tests and examples pass unchanged.
- Phase 1 - OFI transport, commodity providers:
  - `defw_transport_ofi.c`: RDM endpoint, AV, tagged send/receive,
    multi-recv eager buffers, CQ progress thread, per-peer TCP fallback.
  - Session-info extension carrying the OFI endpoint address.
  - Exit criteria: full QFw workflow in the QFw-SLURM-Cluster container
    with `FI_PROVIDER=tcp` and `FI_PROVIDER=shm` (same-node).
- Phase 2 - HPC provider bring-up:
  - Validate `cxi` and `lnx` (shm+cxi) on a Slingshot system using the
    existing environment tuning; validate multi-NIC via
    `FI_LNX_PROV_LINKS`.
  - Measure RPC latency/throughput against the TCP baseline.
  - Exit criteria: QFw examples run on Slingshot with `transport: ofi`.
- Phase 3 - large payloads:
  - Binary attachment API through the Python worker layer.
  - Explicit RMA-read rendezvous for attachments plus MR caching on the
    OFI path; socket streaming on the TCP path.
  - Adopt attachments in one bulk-data service path (statevector
    retrieval) as the proving case.
  - Exit criteria: large-payload transfer benchmark showing RDMA-path
    scaling on Slingshot, and identical results through both transports.

## Testing Strategy

- Unit/loopback: a C-level transport test that sends framed messages
  between two endpoints in one process (both transports), exercising
  eager, provider-rendezvous, and (phase 3) RMA paths.
- Container integration: QFw-SLURM-Cluster already builds libfabric with
  the commodity providers, so the OFI transport is exercised end-to-end
  in CI-like conditions with `FI_PROVIDER=tcp`/`shm` before touching real
  hardware. The existing shim smoke test (`qfw_shim_smoke.sh`) runs
  under both `transport: tcp` and `transport: ofi`.
- Fallback matrix: OFI-enabled client against TCP-only service and vice
  versa; provider-unavailable startup; peer death detection under OFI
  (heartbeat path).
- `DEFw/python/experiments/suite_libfabric/` is repurposed as the
  transport experiment suite (connectivity, message-size sweeps,
  attachment benchmarks).

## Risks And Open Questions

- Liveness semantics: RDM endpoints give no disconnect events, hence
  TCP heartbeats remain load-bearing. A pure-fabric mode (no TCP at all)
  would need HB-over-fabric with timeout-based death detection and is
  out of scope here.
- CXI resource limits: many DEFw processes per node, each opening a CXI
  domain, can exhaust hardware resources. The `lnx` shm+cxi
  configuration mitigates intra-node traffic; needs validation in
  phase 2.
- Send-side buffer lifetime: `fi_tsend()` completion is asynchronous, so
  the transport must hold the message buffer until the send completion
  (today's blocking `write()` loop has no such window). Bounce buffers
  for small messages, completion-tracked buffers for large ones.
- Container provider set: confirm the image's libfabric build enables
  `shm` (built with default providers, so it should; verify with
  `fi_info` in the container).
- Header versioning: the UUID identity change breaks wire compatibility
  with existing builds once, in phase 0. Acceptable for a framework
  deployed as a unit; called out so it lands in a coordinated release.
