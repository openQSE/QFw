# QFw 0.1 Source-Change Report

## Scope and method

This report records the source review and integration outcome for the six
repositories in the coordinated QFw 0.1 release. Comparisons used the
authoritative remote default and `adm-sched-v01` tips fetched with pruning and
tags. The source delta was reviewed from each merge base with triple-dot diffs,
default-only and feature-only logs, file statistics, and targeted API,
configuration, build, test, and documentation inspection.

qhw-data and qhw-iqm have no `adm-sched-v01` branch. Their release branches
therefore select the reviewed `main` commits directly.

## Selected histories

| Component | Default tip | Feature tip | Merge base | Release tip |
| --- | --- | --- | --- | --- |
| qhw-data | `63a24c88739a35bdafab3ef2cea88908f0845fb3` | none | n/a | `63a24c88739a35bdafab3ef2cea88908f0845fb3` |
| qhw-iqm | `e3078979455188e1bda41ac25e280d92214a7d1c` | none | n/a | `e3078979455188e1bda41ac25e280d92214a7d1c` |
| qhw-admission | `fc106c340e74d4859dab30f9beb0cb7fea18dbb0` | `fe5a4a9af3227dcb476ce8722239879a16387d90` | default tip | `47582500fcb1b06f3d32dad6aa78604dfbda67dd` |
| qhw-scheduler | `e5712bbce577af162d6a5bfdfb594de0db7a47eb` | `5e303663d620f371a7a0d7d31f7e5feb2543470d` | default tip | `8cf431d6d64a844dbe04646711f566f7c36572bc` |
| DEFw | `ff9a8917c19f931a3dd866a633f18b487d04932e` | `de9d570d1d6b1393008c8d734783c0736e09c621` | `780474ee2f53889326b3ac41cef0654158739804` | `7728f89673efb96391e6880139ecccc8ce324f1b` |
| QFw | `87895b715b421863b8eaf5635b4dc7ce1c3a72e2` | `ca383b13e0909686c03167a21a3d1c8963d8a633` | `f5a91c6408e4c4053a4f2f12392a8e226bc03215` | code tip `f45fcd8772a759abd8feab933eef8ea2a837ffbc` |

The authoritative remotes are the corresponding repositories under
`git@github.com:openQSE/`. The selected QFw feature tip includes the 12 commits
that appeared local-only during the planning audit. The exact tip was fetched
from GitHub before integration, making that history reproducible.

## qhw-data

There is no branch delta. The selected package provides the provider-neutral
`qhw-result-v1`, `qhw-device-v1`, `qhw-coupling-v1`, and
`qhw-calibration-v1` schemas, validating builders, deterministic JSON support,
optional MessagePack serialization, timestamp normalization, and derived
result durations.

The project is packaged with setuptools for Python 3.10 and newer and includes
its schemas as package data. It has no repository test directory. The release
gate compensated by building wheel and source distributions, installing the
wheel in isolation, validating all bundled examples, and checking JSON and
MessagePack round trips. No integration fix was required.

The main release risk is the absence of a native automated test suite. The
exact commit is tested indirectly by qhw-iqm and the complete QFw stack.

## qhw-iqm

There is no branch delta. The selected package normalizes IQM architecture,
coupling, calibration, native-result, and Qiskit-IQM result dictionaries into
qhw-data records. Its public entry points accept an optional logical device ID
and optional raw-provider retention. Four command-line tools expose the
normalizers.

The package depends on `qhw-data>=0.1.0`, so that lower bound alone does not
identify the coordinated dependency. The QFw gitlink and release manifest pin
the exact qhw-data commit. Release validation installed that wheel first,
exercised all four CLI help paths, and validated representative output from all
normalizers without contacting hardware. No integration fix was required.

## qhw-admission

The feature history is three commits and changes 11 files with 855 insertions
and 18 deletions. It is a direct descendant of the selected default, so no
default-only commit or textual conflict was present.

The public C API gains `qhw_adm_list_reservations`, authoritative snapshots,
pagination, total counts, and filters for device, scope, user, job, state,
workload kind, creation time, and expiry. The Python
`AdmissionContext.list_reservations` wrapper validates and converts this
contract. `qhw_adm_device_profile_t` and its configuration and Python views
gain `max_provider_queue_depth` for provider dispatch-capacity enforcement.

CMake gains a selectable Python installation directory and installs the full
generated package tree, including SWIG and native artifacts. Integration
corrected wheel staging so scikit-build places those artifacts in the
importable package root. The complete 15-test suite, the six-test no-plugin
suite, wheel and installed-package imports, native context creation, and clean
installation all passed.

The added C structure member changes ABI layout. QFw, SWIG bindings, and other
native consumers must be rebuilt as one compatibility set. Existing SWIG
const-string ownership warnings remain non-fatal.

## qhw-scheduler

The feature history is one commit and changes one file with 10 insertions and
32 deletions. It is a direct descendant of the selected default. No scheduler
API, ABI, policy, or algorithm changes are introduced by this branch delta.

The change replaces scattered Python artifact installation with a complete
generated `qhw_scheduler` package-tree install. Integration corrected the
wheel destination and the optional static target's datastructures dependency.
Both normal and static-enabled 23-test suites passed. The pure-static build,
65,536-task workload under 13 policy compositions, wheel import, plugin load,
and isolated installation also passed.

The package-tree install must retain its native links and plugins without
capturing caches. Isolated imports verified this behavior. Existing SWIG
const-string ownership warnings remain non-fatal.

## DEFw

The feature side changes 81 files with 5,029 insertions and 1,975 deletions.
Thirteen commits are default-only and 22 are feature-only, making this a true
two-sided integration.

The default side contributes the TCP/libfabric transport abstraction,
UUID-based sender identity, OFI endpoint exchange, tagged RPC transport,
optional RMA endpoints, attachment traversal, memory registration,
rendezvous, and size-based payload routing. The feature side contributes
explicit directory-service bindings, peer identity and readiness events,
deterministic site-peer selection, service transport reuse, spawned-service
registration, socket cleanup, and mandatory runtime identity. It replaces the
legacy resource-manager service with `api_dirsvc` and `svc_dirsvc`.

The feature also replaces SCons with CMake. The resulting build installs DEFw
libraries, executables, Python runtime, configuration, launchers, CMake package
metadata, SWIG modules, and public typemaps. Tests cover transport, RPC,
directory and binding contracts, peer events, external SWIG generation,
typemap ownership, runner behavior, and the install tree.

Conflicts in `src/defw_agent.h` and `src/defw_listener.c` were resolved by
preserving distinct state bits and combining OFI address learning with peer
ready/lost/removed lifecycles. The default transport sources and RMA bindings
were ported into the feature CMake/SWIG/list model. Release testing exposed an
invalid `FD_ISSET(-1, ...)` during partial connection startup; the release
branch now guards inactive control and RPC descriptors. All 13 DEFw CTests
then passed in both standalone and bundled QFw validation.

The test hosts lacked libfabric development headers and pkg-config metadata,
so the compiled candidate used the tested TCP fallback. The merged OFI/RMA
source and bindings are retained, but compiled OFI validation remains an
infrastructure-dependent limitation.

## QFw

The feature side changes 183 files with 38,531 insertions and 4,428 deletions.
Thirty-four commits are default-only and 124 are feature-only. It adds
qhw-admission and qhw-scheduler as authoritative GitHub submodules and makes a
large API, runtime, installation, and example transition.

Default-only work preserved by the merge includes the DEFw libfabric/RMA
sequence, transport propagation, IQM/QDMI/QRMI comparison support, IQM request
serialization, QDMI job identifiers, direct IQM comparison, timing and
connection reuse measurement, completion-queue identifiers, shim descriptor
configuration, and current flake8 and mock repairs.

The feature splits the QPM contract into common, control, execution,
admission-control, admission-policy, scheduler-control, and telemetry
categories. It adds capability and target selection, request and metadata
tokens, category authorization, reservation metadata, provider credentials,
dispatch limits, lifecycle control, capacity accounting, completion queues,
and scoped telemetry. qhw-admission and qhw-scheduler now govern reservation
and task dispatch end to end.

Qiskit jobs carry reservation context through selection, execution, and
completion. IQM gains constraints, normalized results, credential binding,
telemetry preflight, chemistry wrappers, and long-running site-service
helpers. A fake-IQM QPM shares the production admission and scheduling model
and drives bounded stress scenarios.

The top-level CMake build can bundle DEFw, stages external packages, generates
activation and runtime commands, installs service and API trees, and checks
both source and installed runtimes. Legacy setup fragments are replaced by
`qfw-setup`, `qfw-srun`, `qfw-teardown`, and site/runtime/service YAML.
Examples use managed lifecycle flows and produce machine-readable results.

Actual merge conflicts were resolved across Qiskit lookup, QPM utility and
descriptor code, IQM transcoding, mock fixtures, and the DEFw gitlink. The
release stabilization commits then:

- declared bundled DEFw runtime dependencies and isolated API scans;
- excluded development credential configuration from installation;
- allocated independent local directory and telnet ports;
- used no-secret shim credentials and supported named credential providers;
- normalized VQE and NWQSim statevectors and preserved logical dimensions;
- disabled the unstable persistent NWQSim DVM path for these workloads;
- isolated IQM directory RPC and telnet ports; and
- made an explicit IQM service run directory override activation defaults.

The last item was required after real-IQM preflight proved that the driver was
reading the installed default site file instead of the explicitly selected
runtime site. A dedicated regression test now protects this precedence rule.

At the code tip, shell syntax and flake8 pass, the mock suite reports 192
passed and one skipped, runtime configuration tests report 28 passed, the
Qiskit target test passes, and all 15 bundled QFw/DEFw CTests pass. A recursive
remote clone reconstructed every gitlink and passed the same source, build,
install, import, and credential-exclusion checks.

## Integration and release risks

All intended feature commits are reachable from a published release branch or
from the QFw history that records their merged content. No untracked source
file is a release input. The complete gitlink compatibility set is recorded in
the release manifest.

Native consumers must rebuild for the qhw-admission ABI extension. A
long-running QPM example requires an actual Slurm allocation. The chemistry
example has its own OpenFermion dependency, which is not installed by QFw.
The real-IQM smoke run validates orchestration and hardware execution rather
than scientific convergence. Compiled OFI/libfabric testing remains deferred
to a host with development headers.

The stabilization branches have no configured branch-triggered GitHub Actions
workflows. Remote reproducibility was therefore established with clean clones
and isolated builds. Tag-triggered CI and artifacts remain publication gates.
