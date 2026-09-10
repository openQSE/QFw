# C-Centric Service Interface and RPC Requirements

**Status:** draft

## Purpose and Scope

QFw and DEFw require stable service interfaces that can be called from C,
C++, Python, and framework-specific integrations. The application-facing
interface must not expose the internal RPC mechanism. Service developers
should define an interface once and rely on generated client, server, and
language-binding code.

This document selects an RPC-safe C header as the developer-authored service
definition. The header replaces a separately maintained IDL. DEFw derives an
internal type model from the C abstract syntax tree and generates a private
wire representation. Reusable DEFw C types express common RPC semantics, while
annotations remain available for exceptional cases. The design applies first
to stable QFw and DEFw APIs. The dynamic Python RPC path remains suitable for
experimental and untyped services.

The selected boundary is shown below.

```text
client application
    -> generated or native language binding
    -> canonical C service API
    -> generated DEFw client stub
    -> private DEFw RPC and transport
    -> generated DEFw server dispatcher
    -> C implementation or generated language adapter
    -> service implementation
```

The language-neutral property belongs to the service interface and generated
bindings. The on-wire protocol remains an internal DEFw contract.

## Concluded Architecture

The selected architecture is a code-first C service model. A developer writes a
dedicated header containing the public RPC methods and the types that cross the
remote boundary. The declarations are intentionally designed for RPC and are
separate from unrestricted internal implementation APIs.

The generator invokes Clang with the service build configuration and consumes
the resolved abstract syntax tree. It validates the RPC-safe subset, creates an
internal DEFw type graph, and assigns deterministic service and method
identities. Generated artifacts include the public client implementation,
server dispatch, wire lowering, service metadata, compatibility fingerprints,
and supported language adapters.

```text
designated C service header
    -> Clang parsing and semantic analysis
    -> validated DEFw RPC type graph
    -> generated C client and server code
    -> generated wire and transport lowering
    -> generated Python and other language adapters
```

The developer does not maintain an IDL, Mercury processing declarations, or a
second set of request and response definitions. A service method is remote
because it appears in the designated service header. The service identity and
interface version come from the generator invocation or build configuration.
Internal method numbers and wire fingerprints are generated details.

The wire protocol remains a DEFw black box. Language neutrality is provided by
the stable C interface and its generated bindings. Transport neutrality is
provided by the generated lowering layer and DEFw transport contract. These
two forms of neutrality do not require the wire representation itself to be a
public language-neutral object model.

## Developer Model

A service developer writes a designated C service header with fixed-width
scalar types, RPC-safe structures, and DEFw container types. All exported
function declarations in that header form the remote interface. The generator
receives the service name and interface version as inputs, so ordinary methods
do not require a `DEFW_RPC` marker or a developer-assigned numeric method ID.

Common wire semantics reside in reusable types. An execution request can use a
normal C-facing string view.

```c
typedef struct {
    const char *data;
    uint64_t size;
} defw_string_view_t;

typedef struct {
    uint32_t shots;
    defw_string_view_t qasm;
} qpm_run_request_t;

int qpm_async_run(const qpm_run_request_t *request,
                  qpm_job_t *job);
```

The generator produces a client stub, a server dispatcher, wire types,
conversion routines, registration metadata, and selected language bindings.
A C client calls `qpm_async_run()` as an ordinary function. A Python service
implements a generated method while its adapter performs the C and Python
conversion.

The public C declaration is the authoritative source. Developers do not write
offset calculations, transport calls, or a second schema file. Annotations are
reserved for exceptional semantics that cannot be represented by standard
DEFw types. Internal service and provider APIs are outside this contract and
may use arbitrary implementation-specific C or Python interfaces.

## Internal Type Model

The generator supports a constrained and composable C subset. Fixed-width
integers, floating-point scalars, fixed arrays, nested RPC-safe structures,
and explicitly represented enumerations lower automatically.

Variable data uses standard types such as byte views, string views, typed
array views, owned buffers, bulk buffers, and opaque remote handles. A typed
array view pairs a local typed pointer with an element count. For example,
`defw_u32_array_view_t` represents a borrowed array of `uint32_t` values. A
byte view represents intentionally opaque data. The distinction preserves C
type checking and gives generated bindings the element type needed for
validation and language conversion.

These public views follow the same local-descriptor principle as an `iovec`,
but they provide fixed-width sizes, const correctness, ownership semantics,
and element types. The pointer remains local. Generated code lowers views into
wire descriptors and iovec-like transport segments. POSIX `struct iovec`,
libfabric scatter/gather entries, or bulk handles belong below the public API.

Tagged unions and optional fields use annotations where the C type alone is
ambiguous. External or runtime-specific objects use custom adapters or opaque
handles. Standard view, owned, optional, tensor, and handle types should cover
common RPC interfaces without per-parameter annotations.

Arbitrary pointers, pointer graphs, function pointers, compiler-sized bit
fields, and structures containing platform-dependent types do not lower
automatically. The generator reports these declarations as build-time errors.
This restriction makes memory ownership and wire behavior explicit at the
service boundary.

Complex values are built by composition. Circuit programs can be represented
as tagged unions of QASM, QIR, and compiled-program handles. Statevectors and
matrices use typed tensor descriptors with bulk attachments. Measurement
counts, device topology, and calibration data use arrays of nested records.

## DEFw RPC-Safe C Type Library

DEFw provides a small C type library whose types carry the semantics needed at
an RPC boundary. The exact scalar variants can be generated or declared as
needed, while the ownership and lifetime rules remain uniform.

| Type family | Representative C shape | Meaning at the public API | Generated wire behavior |
| --- | --- | --- | --- |
| `defw_string_view_t` | `const char *data; uint64_t size` | Borrowed, read-only text with an explicit byte length and defined interface encoding. A terminating nul is not required unless the interface says otherwise. | Encode a length and inline offset or attachment reference; reconstruct a call-scoped local view. |
| `defw_bytes_view_t` | `const void *data; uint64_t size` | Borrowed, read-only opaque bytes. The API assigns no element interpretation. | Encode a byte length and inline offset or attachment reference. |
| `defw_u32_array_view_t` and typed peers | `const uint32_t *data; uint64_t count` | Borrowed, read-only array with a compile-time C element type and an element count. | Validate `count * element_size`, encode the element type implicitly from the declaration, and convert elements when the wire ABI requires it. |
| Mutable typed array | `T *data; uint64_t capacity; uint64_t count` | Caller-owned writable storage with an input capacity and output element count. | Validate capacity, copy or transfer returned elements, and report insufficient capacity without overrunning storage. |
| Owned string, bytes, or typed array | `T *data; uint64_t count` | Callee-produced memory whose ownership transfers to the receiving binding. | Allocate or reconstruct the result and provide a generated local release operation. |
| Optional scalar or structure | Presence flag plus value | A value that may be absent without using a pointer as an implicit optional marker. | Encode explicit presence followed by the value when present. |
| `defw_tensor_view_t` | Element type, rank, shape, layout, and data view | A runtime-typed multidimensional value such as a matrix or statevector. | Validate dimensions and size, then use inline or bulk transfer for the data. |
| `defw_bulk_view_t` | Local region, byte count, access intent, and transport-neutral options | A large value eligible for registered-memory or bulk transfer. | Create a transport attachment or fall back to a framed copy without changing the service API. |
| `defw_remote_handle_t` | Stable integer or structured identifier | A reference to remote state that cannot be copied as data. | Transmit the identifier and validate its service, generation, type, and lifetime on use. |
| Job or subscription handle | Typed remote handle | An asynchronous operation or event relationship. | Carry the handle in later status, result, cancellation, or event operations instead of transmitting a function pointer. |

Typed views intentionally differ from a universal `iovec`. An `iovec` provides
a local address and byte length but does not say whether the bytes represent
integers, doubles, complex values, text, or an encoded object. A typed view lets
the C compiler reject mismatched pointers and lets generated Python or C++
bindings reconstruct the intended element type.

The runtime still uses the iovec model internally. Each public view lowers into
one or more transport segments. A TCP implementation can frame or gather those
segments, libfabric can map them to scatter/gather entries, and a bulk path can
register selected regions. Public type safety and transport efficiency therefore
serve different layers of the design.

Reusable types keep annotations exceptional. Tagged-union discriminators,
unusual custom ownership, and third-party objects may need annotations or
adapters. Ordinary strings, arrays, buffers, optional values, tensors, and
remote resources should not require repeated parameter metadata.

## Wire and Invocation Model

DEFw generates canonical wire structures from the public C declarations. A
wire structure uses fixed-width fields, defined byte order, fixed offsets, and
explicit reserved space. It contains no process-local pointers. Variable data
is appended to a framed message or referenced through a DEFw attachment.

The client stub lowers the public request into its generated wire form. The
server validates the frame, reconstructs a local C view, and invokes the
service implementation. Response processing follows the inverse path.

```text
public C request
    -> generated pack operation
    -> fixed wire structure plus inline or bulk data
    -> DEFw transport
    -> generated validation and unpack operation
    -> local C request view
    -> service implementation
```

Generated fixed-layout wire structures may be transmitted as contiguous byte
ranges. This preserves the simple C-centric data path without relying on the
native layout of arbitrary application structures. RHEL and Ubuntu hosts can
interoperate, as can different host architectures, when both implement the
same canonical wire ABI.

Large data remains outside the control message. DEFw chooses an available
bulk path, including a libfabric-capable path, without changing the service
API. The service declaration describes the transfer semantics rather than a
specific communication library.

## End-to-End Generated Call

A QPM execution call illustrates the complete model. The public service header
contains ordinary request and response structures.

```c
typedef struct {
    uint64_t reservation_id;
    defw_string_view_t qasm;
    defw_double_array_view_t parameters;
    defw_u32_array_view_t measured_qubits;
    uint32_t shots;
} qpm_run_request_t;

typedef struct {
    qpm_job_handle_t job;
} qpm_run_response_t;

int qpm_async_run(const qpm_run_request_t *request,
                  qpm_run_response_t *response);
```

The client creates local views over its QASM and arrays, then calls
`qpm_async_run()`. Generated code validates counts and computes the required
payload sizes with checked arithmetic. It creates a fixed control structure and
a segment list containing the control data, QASM, parameters, and measured
qubits. Large segments may become DEFw attachments.

```text
client request structure
    -> generated validation
    -> fixed control descriptor
    -> QASM and typed-array segments
    -> selected DEFw transport
    -> server frame and attachment validation
    -> reconstructed call-scoped C views
    -> qpm_async_run implementation
```

The receiving dispatcher never uses a transmitted pointer value. It creates
local pointers into validated receive storage or materialized attachments. A C
implementation receives the reconstructed request directly. A Python adapter
converts the same values into generated Python objects before invoking the
Python method.

The response follows the reverse path. Scalar handles remain in the control
message. Owned result buffers are reconstructed with explicit ownership and a
generated release operation. Tensor and bulk results retain their type, shape,
and lifetime metadata.

This path hides endpoint lookup, method identity, framing, byte-order handling,
transport selection, attachment management, and response reconstruction from
the application and service implementation.

## Evaluation Against Existing RPC Systems

The approach overlaps with mature RPC systems. Its value depends on the DEFw
runtime and HPC integration rather than code generation alone.

| Approach | Existing strengths | Fit for QFw and DEFw | Cost or limitation |
| --- | --- | --- | --- |
| [gRPC and Protocol Buffers](https://grpc.io/docs/what-is-grpc/introduction/) | Mature service schemas, generated clients and servers, broad language support, streaming, deadlines, cancellation, metadata, and a well-tested HTTP/2 runtime. | Strong choice when ecosystem interoperability and conventional network deployment dominate. | Requires a separate `.proto` source and adopts the gRPC protocol and HTTP/2 transport model. The documented native API is C++, rather than the small stable C ABI desired by QFw. Direct libfabric integration is not a standard transport option. |
| [Protocol Buffers without gRPC](https://protobuf.dev/programming-guides/proto3/) | Mature field-based encoding, compact representation for many values, unknown-field handling, and established schema-evolution rules. | Could provide the payload encoding beneath DEFw while DEFw retains discovery and transport. | Keeps a separate schema, lacks an official plain-C generator, and introduces conversion between generated protobuf objects and the desired C API. |
| [Apache Thrift](https://thrift.apache.org/docs/idl) | IDL-driven service generation, C through GLib, C++, Python, multiple protocols, and an explicit [transport abstraction](https://thrift.apache.org/docs/concepts). | Closer than gRPC to a replaceable transport stack and already solves multi-language generation. | Requires a Thrift IDL and runtime. Its C interface is GLib-oriented, and a production libfabric transport plus QFw directory and reservation integration would still be QFw work. |
| [Mercury and Margo](https://mochi.readthedocs.io/en/latest/mercury.html) | HPC-oriented C RPC, asynchronous progress, complex-structure processing, and [bulk RDMA transfers](https://mochi.readthedocs.io/en/latest/mercury/05_bulk.html). | The closest existing candidate for the native DEFw serialization, RPC progress, and bulk-data runtime. A DEFw generator could emit Mercury processing and registration glue while retaining DEFw discovery and lifecycle. | Mercury-specific input and output declarations remain a schema embedded in macros, and pointer-bearing complex types require custom processing functions. Mercury does not derive an ordinary C API or provide the complete desired multi-language binding workflow. |
| [FlatBuffers](https://flatbuffers.dev/white_paper/) | Cross-platform fixed binary layout, in-place access, nested data, and schema evolution through tables. | Attractive as an internal low-copy payload representation. | Requires an `.fbs` schema and does not provide the complete RPC, service discovery, or reservation runtime. Its design would replace rather than derive from the C header as the source of truth. |

## Advantages

The developer maintains one authoritative service declaration. A change to a
request, response, or method occurs in the C header and flows into the client
stub, server dispatcher, wire conversion, registration metadata, and language
bindings. There is no generated C API that must be reconciled with a separately
authored `.proto`, `.thrift`, `.x`, or macro-based type declaration.

The public result is a small, stable C ABI. C applications link to it directly,
while C++, Python, Rust, CUDA-Q, and framework integrations can wrap the same
contract. A Python binding is a consumer of the C service model rather than the
definition of the remote interface. Python version selection no longer
determines the QFw wire contract or QPM implementation language.

Clang supplies mature C parsing, preprocessing, typedef resolution, attribute
handling, and source diagnostics. DEFw implements the RPC-specific lowering
rules instead of maintaining a C grammar. Unsupported declarations can be
reported against their exact source locations.

DEFw retains control of communication. TCP, libfabric, shared memory, or a
future transport can implement the same internal send and bulk operations. The
application and service APIs remain unchanged when a site selects another
transport. Public views lower naturally into scatter/gather segments, while
large values can use registered memory or bulk transfers.

Generated fixed-layout control messages can reduce allocations, reflection,
and intermediate object construction. Bulk values avoid unnecessary copies
through a general-purpose message representation. These properties can reduce
CPU overhead and improve predictable latency.

The wire-size advantage is workload dependent. Protobuf can encode small
integers compactly and omit default-valued fields. Fixed-layout messages may
use more bytes because widths and reserved space remain present. The expected
benefit is lower conversion cost, direct scatter/gather lowering, and better
bulk-path integration rather than a guarantee that every message is smaller.

The approach places unavoidable RPC constraints in familiar C abstractions.
A typed view, owned buffer, optional value, tensor, or remote handle expresses
semantics once and can be reused across APIs. This is more concise than
repeating pointer annotations and more natural for a C developer than learning
a second schema language.

The design preserves DEFw-specific capabilities. Directory discovery, runtime
UUIDs, service generations, reservation-aware QPM selection, dynamic Python
services, service lifecycle, and HPC placement remain part of one runtime. An
external serializer or RPC engine may be reused beneath DEFw without replacing
that control plane.

## Costs and Risks

The C header removes a separate developer-authored IDL, but it does not remove
schema work. DEFw must maintain an internal type model, Clang-based analysis,
code-generation backends, compatibility rules, and diagnostics. This is a
compiler-tooling commitment rather than a small serialization helper.

Mature RPC systems already provide extensive malformed-input testing,
cross-language conformance, deadlines, cancellation, streaming, reflection,
authentication integration, tracing hooks, and debugging tools. DEFw must
implement the subset required by QFw and test it across every supported
transport and binding.

The constrained C subset may surprise developers expecting arbitrary C types
to work. Clear generated diagnostics, standard container types, and custom
adapter escape hatches are required for a practical development experience.

Version evolution is more demanding than it is with protobuf fields or
FlatBuffers tables. Each interface needs stable method identifiers, explicit
major and minor versions, reserved fields, and compatibility fingerprints.
Incompatible endpoints must fail during binding rather than misinterpret a
message.

Language neutrality depends on maintained bindings. A language without a DEFw
binding cannot consume the private protocol independently. This is acceptable
for a controlled QFw deployment but weaker than the open interoperability of
gRPC, protobuf, or Thrift.

Security boundaries remain important in a managed cluster. The server must
validate method identifiers, frame sizes, offsets, counts, attachment lengths,
integer arithmetic, and reservation context before creating local views.
Direct structure transfer does not make client input trustworthy.

## Recommendation and Adoption Gate

Proceed with a bounded prototype rather than committing all QFw APIs to a new
generator immediately. The prototype should cover directory lookup,
`QPMControl.is_ready()`, and `QPMExecution.async_run()`. The execution request
should include a small control structure and an inline or bulk QASM payload.
A result should exercise a structured response and a large attachment.

The prototype must compare the proposed implementation with gRPC/protobuf and
Mercury/Margo. Measurements should include generated API usability, control
message latency, CPU cost, allocation count, wire bytes, bulk-transfer
throughput, cross-distribution operation, failure diagnostics, build
dependencies, and maintenance scope.

The design should proceed to full API coverage only when the prototype shows a
material benefit from preserving DEFw transport and lifecycle integration.
Mercury/Margo should be adopted or reused beneath DEFw if it supplies the
required native RPC and bulk behavior with less long-term maintenance.

## RPC Interface Design Practice

Established RPC systems define interfaces for transport rather than expose
arbitrary implementation APIs.
[gRPC](https://grpc.io/docs/what-is-grpc/core-concepts/) uses schema-defined
request and response messages. [Thrift](https://thrift.apache.org/docs/idl)
limits methods to its portable type system, and its argument lists are
represented as structures.
[Cap'n Proto](https://capnproto.org/language.html) uses schema-defined
parameters and results.
[D-Bus](https://dbus.freedesktop.org/doc/dbus-specification.html) requires
method signatures from its wire type system.

C-oriented systems follow the same pattern.
[Mercury](https://mochi.readthedocs.io/en/latest/mercury/04_args.html) requires
known input and output processors, while pointer-bearing complex types need
explicit encode, decode, and free behavior.
[ONC RPC](https://docs.oracle.com/cd/E18752_01/pdf/816-1435.pdf) uses a
restricted C-like RPC language and generates C stubs and XDR conversion code.

Code-first systems infer rather than eliminate the wire contract. Java RMI
accepts primitives, remote references, and serializable objects. WCF infers or
annotates data contracts and imposes serialization rules on members and known
types. Their native object models simplify same-runtime RPC while increasing
runtime coupling.

DEFw follows the common practice of designing an RPC-specific public boundary.
Its distinction is that the boundary is an ordinary constrained C API rather
than a separately authored IDL. Request and response structures carry the
wire-relevant semantics through reusable C types. Internal provider and service
implementation APIs remain unrestricted and connect through generated server
adapters.

## C Interface Requirements

| ID | Requirement |
| --- | --- |
| CAPI-001 | Stable QFw and DEFw service interfaces shall be authored as C headers without a separately maintained service IDL. |
| CAPI-002 | The canonical application-facing interface shall use a stable C ABI that can be consumed directly by C and wrapped by other languages. |
| CAPI-003 | The public service header shall define methods, request types, response types, status values, and ownership semantics needed to invoke the service. |
| CAPI-004 | A service implementation shall be permitted to use C or a generated adapter for another supported language, including Python. |
| CAPI-005 | The generated client API shall hide service discovery, binding, RPC framing, transport selection, and response reconstruction from the application. |
| CAPI-006 | Stable service APIs shall be organized by interface category, including directory, QPM execution, QPM admission control, QPM control, scheduler control, and telemetry. |
| CAPI-007 | The dynamic Python RPC mechanism may coexist as a separate path for experimental or untyped DEFw services and shall not define the stable cross-language QFw API contract. |
| CAPI-008 | A designated service header shall define the remote interface through its exported function declarations without requiring a per-method DEFw RPC marker or developer-assigned numeric method ID. |
| CAPI-009 | The generator invocation or service build configuration shall provide the service identity and interface version associated with a designated service header. |
| CAPI-010 | RPC-safe C restrictions shall apply to public remote service headers and shall not constrain private service, provider, scheduler, or application implementation APIs. |
| CAPI-011 | Stable RPC methods should accept an RPC-safe request structure and produce an RPC-safe response structure so that evolution, validation, ownership, and generated binding behavior remain explicit at the service boundary. |

## Generator Requirements

| ID | Requirement |
| --- | --- |
| CGEN-001 | The DEFw service generator shall consume the compiler-resolved C abstract syntax tree rather than implement an independent C parser. |
| CGEN-002 | The generator shall lower supported C declarations into an internal DEFw type model used by every generated language and wire backend. |
| CGEN-003 | The generator shall produce C client stubs, server dispatchers, wire definitions, pack and unpack operations, registration metadata, interface fingerprints, and requested language bindings. |
| CGEN-004 | Generated output shall be deterministic for the same input headers, generator version, and configuration. |
| CGEN-005 | The generator shall reject unsupported or ambiguous declarations at build time and identify the source declaration and required correction. |
| CGEN-006 | The generator shall support custom type adapters without requiring a custom serializer for the containing request or response. |
| CGEN-007 | Generated code shall expose enough source and method metadata to diagnose an RPC failure without exposing application secrets or bulk payload contents. |
| CGEN-008 | The generator shall derive a deterministic internal method identity from the service identity, interface version, and exported function declaration without requiring the method identifier in the public function signature. |

## C Type Requirements

| ID | Requirement |
| --- | --- |
| CTYPE-001 | Automatically lowered scalar fields shall use explicitly supported fixed-width integer and floating-point types. |
| CTYPE-002 | The type model shall support nested RPC-safe structures, fixed arrays, explicitly represented enumerations, optional values, and tagged unions. |
| CTYPE-003 | Variable-sized values shall use defined DEFw string, byte, typed-array, owned-buffer, bulk-buffer, or equivalent container contracts. |
| CTYPE-004 | Pointer-bearing declarations shall express element count, direction, nullability, lifetime, and ownership through a standard DEFw type or annotation. |
| CTYPE-005 | External objects and nonportable runtime objects shall cross the interface through an opaque handle, a bulk descriptor, or an explicitly registered custom adapter. |
| CTYPE-006 | The generator shall reject unannotated pointers, function pointers, compiler-sized bit fields, and platform-dependent scalar types in wire-visible declarations. |
| CTYPE-007 | The generated binding shall define whether each reconstructed view is call-scoped, caller-owned, callee-owned, or transferred. |
| CTYPE-008 | DEFw shall provide typed borrowed array views that pair a const element pointer with a fixed-width element count and preserve the element type in the public C API. |
| CTYPE-009 | DEFw shall provide an untyped byte view for values whose API semantics intentionally describe opaque bytes rather than a typed array. |
| CTYPE-010 | Common RPC interfaces shall express size, ownership, mutability, optionality, tensor shape, and remote-resource semantics through reusable DEFw types; parameter annotations shall be reserved for semantics that those types cannot express. |
| CTYPE-011 | The DEFw string-view contract shall pair a borrowed const character pointer with a fixed-width byte length and shall define the text encoding and nul-termination behavior independently of the local pointer representation. |
| CTYPE-012 | The DEFw byte-view contract shall pair a borrowed const pointer with a fixed-width byte length and shall represent intentionally opaque data without an element-type claim. |
| CTYPE-013 | A mutable array or buffer contract shall distinguish caller-provided capacity from the element or byte count produced by the remote operation. |
| CTYPE-014 | An owned string, byte buffer, or typed array contract shall identify transferred ownership and shall have a generated local release operation for every supported client language. |
| CTYPE-015 | Optional RPC values shall carry explicit presence state rather than infer absence from an otherwise ambiguous pointer value. |
| CTYPE-016 | A tensor view shall define element type, rank, dimensions, layout, data length, and local data view so that matrices, statevectors, and other multidimensional values can be validated and bound across languages. |
| CTYPE-017 | A bulk view shall describe a local memory region and access intent without exposing a specific transport, allowing DEFw to select registered-memory transfer or a framed-copy fallback. |
| CTYPE-018 | Remote objects and asynchronous operations shall use typed handles whose validation includes the owning service, service generation, object type, and lifetime state. |

## Wire Requirements

| ID | Requirement |
| --- | --- |
| CWIRE-001 | The DEFw wire protocol shall remain an internal runtime contract; applications and service implementations shall depend on generated interfaces rather than wire details. |
| CWIRE-002 | Generated wire structures shall use fixed field widths, defined byte order, fixed offsets, explicit reserved space, and no process-local pointers. |
| CWIRE-003 | DEFw may transmit a generated fixed-layout wire structure as a contiguous byte range when the structure satisfies the canonical wire ABI. |
| CWIRE-004 | Variable-sized data shall be represented by validated lengths and offsets within a framed message or by validated DEFw attachment descriptors. |
| CWIRE-005 | Large circuits, statevectors, matrices, calibration arrays, and comparable values shall be eligible for a transport-specific bulk path without changing the public service API. |
| CWIRE-006 | Every binding shall verify the interface version and compatibility fingerprint before invoking a method. |
| CWIRE-007 | An incompatible interface or wire version shall produce a structured binding error and shall not attempt best-effort interpretation. |
| CWIRE-008 | The server shall validate frame size, method identifier, offsets, counts, attachment bounds, integer arithmetic, and relevant reservation context before invoking an implementation. |
| CWIRE-009 | Generated initialization and packing code shall initialize reserved bytes and shall not transmit uninitialized process memory. |
| CWIRE-010 | Generated stubs shall lower public string, byte, typed-array, tensor, and bulk views into validated wire descriptors and iovec-like transport segments without transmitting their local pointer values. |
| CWIRE-011 | POSIX iovec structures, libfabric scatter/gather entries, and equivalent transport descriptors shall remain internal transport representations rather than the universal public representation of typed arrays. |

## Runtime and Binding Requirements

| ID | Requirement |
| --- | --- |
| CBIND-001 | Client runtime initialization shall locate the DEFw directory service and resolve the QPM services represented by the application's reservation context. |
| CBIND-002 | Generated bindings shall preserve the same method semantics, status values, ownership rules, and error categories across supported languages. |
| CBIND-003 | A generated Python server adapter shall reconstruct Python values, invoke the corresponding implementation method, and lower its result without exposing RPC framing to the implementation. |
| CBIND-004 | Asynchronous methods shall use defined job, future, event, or subscription handles rather than transmitting process-local callbacks. |
| CBIND-005 | The runtime shall support timeouts, cancellation, structured errors, and correlation identifiers for generated RPC calls. |
| CBIND-006 | A transport implementation shall satisfy one internal DEFw RPC and bulk-transfer contract so that service headers and generated application bindings remain transport independent. |
| CBIND-007 | DEFw shall support its required TCP path and libfabric-capable path through the same generated service interface. |
| CBIND-008 | Generated release operations for client-owned reconstructed results shall be local binding operations and shall not be exposed as remote service methods. |

## Compatibility and Validation Requirements

| ID | Requirement |
| --- | --- |
| CVAL-001 | Interface methods and wire-visible fields shall have stable identifiers and explicit versioning rules. |
| CVAL-002 | Removed method and field identifiers shall not be reused within the same interface major version. |
| CVAL-003 | Generated pack and unpack operations shall have round-trip, malformed-input, bounds, overflow, and fuzz tests. |
| CVAL-004 | Binding conformance tests shall invoke each service across C and Python clients and C and Python implementations where those combinations are supported. |
| CVAL-005 | Interoperability tests shall cover the supported RHEL and Ubuntu deployment combinations and each supported host architecture. |
| CVAL-006 | Transport conformance tests shall run the same generated API suite over every supported DEFw transport. |
| CVAL-007 | The initial prototype shall be benchmarked against gRPC/protobuf and Mercury/Margo before the generator is adopted for the complete QFw API surface. |
| CVAL-008 | The adoption review shall evaluate developer effort, generated-code complexity, latency, CPU time, allocations, wire size, bulk throughput, schema evolution, diagnostics, security, dependencies, and expected maintenance cost. |
| CVAL-009 | The prototype evaluation shall measure annotation burden across representative directory, QPM control, execution, admission, scheduler, and telemetry headers; common methods shall remain expressible through ordinary request and response structures and reusable DEFw types. |
