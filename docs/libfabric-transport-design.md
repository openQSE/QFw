# Libfabric Transport Design

## Table Of Contents

- [Purpose](#purpose)
- [Implementation Status](#implementation-status)
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

## Implementation Status

Phases 0, 1 and 3 are implemented and validated in the QFw-SLURM-Cluster
container over the `tcp` and `sm2` providers. Phase 2 (Slingshot bring-up)
has not started.

This document was written before any of it was built, and several decisions
changed once the code met real providers. It has since been reconciled with
what exists: the sections below describe the implemented behavior in the
present tense, and phase 2 remains a plan. Where the original design was
revised, the reason is recorded, because the reasons tend to be provider
behavior that the next person will also run into.

The changes worth knowing about, if you read an earlier revision of this
document:

- Tagged messaging was dropped. Messages carry a header that already
  identifies the type, so `FI_MSG` with `fi_send`/`fi_recv` is used rather
  than `FI_TAGGED` with `fi_tsend`.
- Eager receive buffers are a fixed-size pool, not `FI_MULTI_RECV`, and a
  message too large for one is **truncated**, not carried by a provider
  rendezvous. Oversized messages are sent over TCP instead.
- The RMA descriptor addresses a region by virtual address *or* by offset
  depending on what the provider negotiates, and the memory-registration key
  is not always the provider's to choose.
- Attachments are detected transparently rather than passed as an explicit
  argument, so no service code changes to benefit.
- Memory-registration caching is not implemented.

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
  (`cxi`), InfiniBand (`verbs;ofi_rxm`), intra-node shared memory (`sm2`),
  and linked multi-provider configurations (`lnx`).
- Keep TCP fully working as the default transport and as a runtime
  fallback when no usable OFI provider exists.
- Keep the Python infrastructure (`defw_workers.py`, `defw_agent.py`,
  `defw_remote.py`) and all service code unchanged.
- Provide an RDMA path for large binary payloads that bypasses YAML
  serialization, without service code having to ask for it.

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

### Reliable datagram endpoints

One `FI_EP_RDM` endpoint per DEFw process, with peers addressed through an
address vector, replaces per-peer sockets for fabric traffic. This matches
how the relevant providers (`cxi`, `verbs;ofi_rxm`, `sm2`, `lnx`, `tcp`)
are designed to be used and avoids connection-management state.

This was originally specified with tagged messaging, the channel and
message type encoded in the tag standing in for the two-socket CTRL/RPC
split. Tagging was dropped during implementation: the framed header already
carries the message type and the sender's uuid, so a tag would have been a
second copy of information the receive path has to read anyway, and
dispatching on the header lets the fabric and socket paths share one
dispatch table.

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

- `defw_agent_blk_t` carries the peer's `fi_addr_t` (as a `uint64_t`, so
  the header needs no libfabric include) alongside the existing TCP fds,
  guarded by a `DEFW_AGENT_OFI_ADDR_VALID` state bit. A union was
  considered and rejected: both are live at once, because the control
  channel stays on TCP while RPC traffic uses the fabric.
- The `progress()` op is declared but unused. The intent was to reduce the
  listener thread to `while (!shutdown) transport->progress();` but the
  TCP listener keeps its `select()` loop and OFI drains its receive CQ on
  its own thread, since the two need to run concurrently in a process that
  is on the fabric for RPC and on TCP for heartbeats. Unifying them is
  possible but has no caller today.
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
  Capabilities requested in `fi_getinfo()` hints: `FI_MSG | FI_RMA |
  FI_READ | FI_REMOTE_READ`, falling back to `FI_MSG` alone when the
  provider offers no RMA. See
  [Large Payloads And RDMA](#large-payloads-and-rdma) for what that costs.
- Addressing: `FI_AV_MAP` address vector. Each peer's `fi_getname()` blob
  is exchanged over the TCP session handshake and inserted with
  `fi_av_insert()`; the resulting `fi_addr_t` is stored in the agent
  block.
- Message framing: `defw_message_hdr_t` is sent as a prefix in the same
  buffer as the payload, one `fi_send()` per message. RDM messages are
  atomic units, so the header/body split reads that the TCP code needs do
  not apply.
- Messaging is untagged. An earlier revision of this design demultiplexed
  on a tag encoding `[channel:8][msg_type:8][reserved:48]`, but the framed
  header already carries the message type and the sender's uuid, so the
  receive path dispatches on the header exactly as the TCP path does and
  shares its dispatch table. The tag helpers remain in `defw_transport.h`
  for a future transport that wants them. Nothing uses them today.
- Receive buffers: a fixed pool of pre-posted eager buffers (8 of 256 KiB),
  each re-posted as soon as its message has been copied out.
  `FI_MULTI_RECV` is not used.
- **A message larger than one eager buffer is truncated**, and the sender
  is not told: it shows up at the far end as a request that never arrives.
  The transport therefore sends any framed message that would exceed the
  eager buffer over TCP instead, which is a stream and has no such limit.
  Large payloads normally travel as RMA attachments and never approach
  this, so the TCP path is the fallback for payloads that stay inline.
  Note that this is a property of DEFw's own buffers, not of the provider:
  a provider-internal rendezvous protocol (`FI_CXI_RDZV_*` on `cxi`) does
  not rescue a receive that was posted with too small a buffer.
- Progress: the progress thread blocks in `fi_cq_sread()` with a one second
  timeout, short enough to notice shutdown promptly. Data progress is left
  to the provider rather than requested in the hints. The `tcp` provider
  reports `FI_PROGRESS_AUTO`, which is why an RMA read completes without
  the target being inside a libfabric call.
- Threading: `fi_send()` is called from Python worker threads while the
  progress thread polls the receive CQ. The domain is opened with
  `FI_THREAD_SAFE`.
- Completion handling: transmit and receive completions go to separate
  CQs, so a blocking send does not consume a completion the progress
  thread is waiting for. A send issues one operation and waits for its own
  completion under a lock, which keeps exactly one completion outstanding
  on the transmit CQ. The RMA read path takes the same lock for the same
  reason. Receive completions carry the buffer to the shared dispatch
  table.
- Completion errors are reported with both libfabric's own error code and
  the provider's. The provider code alone is zero for anything the library
  detects itself, which renders a truncated receive as "Success".

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
   `DEFW_AGENT_OFI_ADDR_VALID`.
4. RPC traffic (`EN_MSG_TYPE_PY_REQUEST` / `PY_RESPONSE` / `PY_EVENT`)
   for that peer is sent with `fi_send()` from then on. Heartbeats and
   session messages stay on the TCP CTRL socket.
5. If either side advertised an empty OFI address, the pair keeps using
   the TCP RPC channel (today's second socket). This is the fallback and
   also the behavior of a TCP-only build.

Notes:

- The separate TCP RPC socket is established for every peer, including
  peers reached over the fabric. The original intent was to open it only
  in fallback mode and so halve the sockets per peer, and that saving is
  still available, but the socket is now load-bearing: it is what carries
  a message too large for the eager receive buffer. Removing it would
  need the oversized-message path handled some other way first.
- DEFw-resmgr needs no changes: it already brokers agent discovery, and
  the OFI address rides inside the existing per-pair handshake, not
  through the resmgr. This is deliberately simpler than the
  `suite_libfabric` experiment, which brokered contexts through the
  resmgr; the per-pair handshake works even for direct client-to-service
  connections that bypass the resmgr.

## Large Payloads And RDMA

Two separate problems have to be solved for very large data:

1. Serialization. A large result used to be YAML-encoded in Python, copied
   into a C string, sent, and parsed back into Python objects. RDMA
   underneath that encoding would gain little. Large buffer-protocol
   objects (NumPy arrays, and `bytes`/`bytearray` above a threshold) are
   therefore lifted out of the message before it is serialized, leaving a
   marker recording enough to reconstruct the object, and restored on the
   receiving side.

   This is transparent rather than an opt-in argument. A service that
   returns a NumPy array keeps returning a NumPy array and needs no
   change. The framework notices the payload. The alternative considered
   was an explicit attachments argument on `send_req()`, which was
   rejected because it makes every bulk-data call site opt in by hand for
   no gain in expressiveness.

2. Transfer. Each payload travels one of two ways, chosen per payload:

   - **Inline.** The payload is base64-encoded into the YAML body. This
     works on every transport and is the right choice below the size where
     an extra round trip costs more than the encoding.
   - **RMA read.** The sender registers the buffer (`fi_mr_reg`) and puts a
     descriptor in the message in place of the data. The receiver pulls it
     with `fi_read()` and sends a short acknowledgement (a new message
     type) so the sender can deregister.

   A message can carry both, so a small array riding along with a large one
   does not pay for a registration and a round trip.

### What the descriptor has to absorb

The descriptor is `{handle, key, addr, len}`. `key`, `addr` and `len` are
what the peer needs. `handle` is the sender's own registration id, echoed
back in the acknowledgement so it knows what to release.

`addr` is **not** simply the buffer's base address, and this is the part
most likely to catch someone out. What a peer must pass to `fi_read()`
depends on the memory-registration mode the provider negotiates:

- With `FI_MR_VIRT_ADDR`, the region is named by the sender's virtual
  address.
- Without it, the region is named by an **offset** from the start of the
  region, so a whole-buffer read passes zero.

The container's `tcp` provider negotiates `mr_mode = 0` and so wants
offsets. Naming the region by its virtual address fails the read with
`ENOENT`. The registering side resolves this into `addr` at registration
time, so the reading peer needs no knowledge of the provider at all.

Keys have a matching subtlety. With `FI_MR_PROV_KEY` the provider invents
the key and ignores the requested one. Without it the key is ours to choose
and must be unique among live registrations. Passing a fresh key from a
counter and then reading back `fi_mr_key()` is correct either way, and a
collision is not silent (`fi_mr_reg` fails with `FI_ENOKEY`).

### Providers that cannot do this

Two cases fall back to inline rather than failing:

- The provider offers no `FI_RMA` at all. The `sm2` shared-memory provider
  is one. `fi_getinfo()` returns `ENODATA` for the RMA hints, so the
  endpoint is opened message-only.
- The provider requires `FI_MR_LOCAL`, meaning the *receiver's* destination
  buffer must also be registered and passed as a descriptor. DEFw does not
  do that yet, so it disables RMA and logs why rather than issuing reads
  the provider will reject. This is the likely situation on `cxi`, and is
  where phase 2 will have to pick it up.

Because the fallback is inline, and inline payloads can exceed the eager
receive buffer, a large payload on a non-RMA provider ends up on the TCP
path described in [Libfabric Mapping](#libfabric-mapping). That is slower
but correct, and it is the reason the per-peer TCP RPC socket still exists.

### Not implemented

A memory-registration cache. `fi_mr_reg` is inexpensive on the `tcp`
provider, so caching would pay off only on a NIC provider, where it should
be decided with phase 2 measurements in hand rather than guessed at now.
`FI_MR_CACHE_*` tuning already exists in
`environment/qfw_libfabric_env.sh` for whatever libfabric itself caches.

Zero-copy publishing. A payload is currently copied twice, once into
`bytes` and once into the registered region. Registering the Python
object's memory in place needs a buffer-protocol typemap and a way to keep
the object alive until the acknowledgement arrives.

## Configuration

- Install configuration (`setup/config/*.yaml`): the existing
  `libfabric-install` key locates the libfabric installation.
Configuration is by environment variable, following the existing `DEFW_*`
pattern. The `transport:` and `ofi-provider:` service-YAML keys originally
proposed here were deferred: the DEFw YAML files interpolate the
environment anyway, so the variable has to reach the agent regardless, and
adding a second place to say the same thing earns nothing until there is a
reason to vary it per service.

- `DEFW_TRANSPORT` - `tcp` (default) or `ofi`. With `ofi`, agents attempt
  OFI and fall back to TCP per peer as described above.
- `DEFW_OFI_PROVIDER` - optional provider constraint (`tcp`, `sm2`, `cxi`,
  `lnx`, `verbs`); empty means take `fi_getinfo()`'s best match. The
  standard libfabric `FI_PROVIDER` filter also works, since provider
  selection flows through `fi_getinfo()`.
- `DEFW_RMA_ATTACHMENTS` - set to `0` to keep every payload inline
  regardless of size. RMA is used by default wherever it is available, so
  nothing has to be switched on.
- `DEFW_RMA_THRESHOLD` - payload size in bytes at which RMA takes over from
  inline. The default is 64 KiB. Setting it to `0` sends every attachment by RMA,
  which is how the RMA path is exercised with small payloads.
- `DEFW_OFI_RMA_SELFTEST` - set to `1` to run a loopback `fi_read` against
  the process's own endpoint at startup. Diagnostic, and off by default.

The 64 KiB default is a judgement, not a measurement. Base64 inflates a
payload by a third and the eager receive buffer is 256 KiB, so an inline
payload much above 192 KiB leaves the eager path regardless. 64 KiB sits
well below that and well above the small arrays that ride along with
ordinary RPCs. It is worth revisiting with numbers when a bulk-data path
adopts attachments.

Because RMA is on by default, no new variable has to be threaded through
the QFw launcher's curated environment (`get_external_defw_env()` in
`setup/qfw_setup.py`) to reach the resmgr and service agents.
`DEFW_TRANSPORT` and `DEFW_OFI_PROVIDER` do, and are already listed there.

Slingshot tuning stays where it is today, in
`environment/qfw_libfabric_env.sh` (`FI_LNX_PROV_LINKS`, `FI_CXI_RDZV_*`,
and related variables).

## Build Integration

- SCons: `defw_transport_ofi.c` compiles only when libfabric is found
  (via `LIBFABRIC_DIR` / `PKG_CONFIG_PATH`, both already exported by
  `qfw_libfabric_env.sh`). Without libfabric, DEFw builds TCP-only, so
  the new transport adds no hard dependency. Minimum supported libfabric
  is 1.20; the container ships 2.3.1.
- A TCP-only build and an OFI-enabled build are wire-compatible with
  each other (the OFI-enabled build simply advertises an OFI address
  when it has one, and a TCP-only peer advertises none).
- The stale `swigify` externals entry in `defw_build.yaml` (absolute paths
  into a private libfabric tree) is still there and still wants removing.
- SWIG changes were needed after all, for the RMA attachment path only.
  Phases 0 to 2 leave the Python/C interface alone, but moving a binary
  payload across it needs typemaps: the generic `char*` mapping produces a
  NUL-terminated string and truncates binary data at the first zero byte.
  `uint64_t` is also avoided in the wrapped prototypes, because SWIG wraps
  it as an opaque pointer object rather than a Python integer.

## Phased Implementation Plan

Each phase is independently mergeable, and phases 1-3 are opt-in behind
`DEFW_TRANSPORT`. Phase 3 was taken before phase 2, so that the RMA path
was in hand before Slingshot bring-up rather than after it.

- Phase 0 - transport abstraction (no behavior change). **Done.**
  - Introduce `defw_transport.h` and move the existing TCP code behind
    it (`defw_transport_tcp.c`).
  - Replace the header IP identity check with the UUID identity check;
    bump `DEFW_VERSION_NUMBER`.
  - Exit criteria: existing smoke tests and examples pass unchanged.
- Phase 1 - OFI transport, commodity providers. **Done.**
  - `defw_transport_ofi.c`: RDM endpoint, AV, send/receive, eager receive
    buffer pool, CQ progress thread, per-peer TCP fallback.
  - Session-info extension carrying the OFI endpoint address.
  - Exit criteria: full QFw workflow in the QFw-SLURM-Cluster container
    over the `tcp` and `sm2` providers. Note `sm2`, not `shm`: see
    [Risks And Open Questions](#risks-and-open-questions).
- Phase 2 - HPC provider bring-up. **Not started.**
  - Validate `cxi` and `lnx` (shm+cxi) on a Slingshot system using the
    existing environment tuning; validate multi-NIC via
    `FI_LNX_PROV_LINKS`.
  - Expect `cxi` to require `FI_MR_LOCAL`, which currently disables the
    RMA attachment path. Registering the read's destination buffer and
    passing `fi_mr_desc()` is the work that unblocks it.
  - Measure RPC latency/throughput against the TCP baseline.
  - Exit criteria: QFw examples run on Slingshot with `DEFW_TRANSPORT=ofi`.
- Phase 3 - large payloads. **Transfer path done. Adoption and benchmark
  outstanding.**
  - Transparent attachment detection in the Python worker layer.
  - Explicit RMA-read rendezvous on the OFI path, inline base64 otherwise,
    chosen per payload by size.
  - Memory-registration caching: deferred, see
    [Large Payloads And RDMA](#large-payloads-and-rdma).
  - Still to do: adopt attachments in one bulk-data service path
    (statevector retrieval) as the proving case.
  - Exit criteria: large-payload transfer benchmark showing RDMA-path
    scaling on Slingshot, and identical results through both transports.
    The second half holds in the container today. The first is gated on
    phase 2 hardware, so phase 3 cannot formally close before phase 2
    even though the transfer path is complete.

## Testing Strategy

- Loopback: `DEFW_OFI_RMA_SELFTEST=1` registers a buffer and reads it back
  out of the process's own endpoint through the real registration and read
  paths, so a provider whose addressing or registration rules were misread
  fails at startup with a clear message instead of corrupting a payload
  later. The broader C-level two-endpoint test originally planned here does
  not exist. The container integration below covers the same ground with
  real agents.
- Container integration: QFw-SLURM-Cluster already builds libfabric with
  the commodity providers, so the OFI transport is exercised end-to-end in
  CI-like conditions over `tcp` and `sm2` before touching real hardware.
- Attachment routing: the choice between inline and RMA is decided in pure
  Python, so it is covered by a standalone test against a stubbed C layer
  that needs neither libfabric nor a peer. The transfer itself is covered
  in the container by round-tripping an array through a real two-process
  RPC, with the receiving side reporting a checksum of what arrived so a
  failure in the request direction is distinguishable from one in the
  response direction. The matrix worth keeping: large payload with and
  without RMA, small payload, both in one message, a provider with no RMA,
  and the threshold forced to zero.
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
- Send-side buffer lifetime: resolved by blocking. A send waits for its own
  completion before returning, so the message buffer is safe to free and
  there is no window to manage. That costs concurrency, which the separate
  transmit CQ and the send lock make safe rather than fast. Funnelling
  sends through the progress thread remains the optimization if profiling
  ever asks for it.
- Operation context lifetime: a context passed to `fi_send()` or
  `fi_read()` must stay valid until the operation completes, which a
  *timeout* does not guarantee. The operation is still outstanding and
  the provider may write the completion afterwards. Contexts therefore live
  in the transport state, not on the caller's stack. The destination buffer
  of a timed-out read has the same exposure and is not yet handled.
  Cancelling the operation would be the correct fix.
- Container provider set: resolved, and the answer was a name. The image's
  libfabric 2.3.1 does build `shm`, but the legacy `shm` provider
  advertises nothing on this version. `fi_info -p shm` returns `ENODATA`
  even with no other hints. The gen-2 shared-memory provider is **`sm2`**,
  which accepts the RDM and `FI_MSG` hints. Use `DEFW_OFI_PROVIDER=sm2`.
  `sm2` offers no `FI_RMA`, so it exercises the non-RMA fallback.
- Reading `fi_info` output: provider records go to **stderr** while the
  environment-variable help goes to stdout, so redirect both. `-c` takes a
  single capability. A comma-separated list is an argument error rather
  than a no-match, which reads like a negative result but is not one.
- Header versioning: the UUID identity change breaks wire compatibility
  with existing builds once, in phase 0. Acceptable for a framework
  deployed as a unit; called out so it lands in a coordinated release.
