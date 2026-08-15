# QFw v0.1.0-rc.1 Release Manifest

## Candidate identity

- Release branch: `release/v0.1`
- Planned annotated tag: `v0.1.0-rc.1`
- Validation date: 2026-08-15
- Publication state: not tagged; default branches not advanced

The QFw code revision validated before adding release metadata is
`f45fcd8772a759abd8feab933eef8ea2a837ffbc`. The final QFw candidate is the
commit containing this manifest and the release notes. Documentation-only
validation is repeated on that commit before publication. The annotated QFw
tag will provide its immutable exact identity without attempting an impossible
self-reference inside the tagged commit.

## Component set

| Component | Remote | Default branch and inventory tip | Feature tip | Validated release tip | Planned tag |
| --- | --- | --- | --- | --- | --- |
| qhw-data | `git@github.com:openQSE/qhw-data.git` | `main` at `63a24c88739a35bdafab3ef2cea88908f0845fb3` | none | `63a24c88739a35bdafab3ef2cea88908f0845fb3` | `v0.1.0-rc.1` |
| qhw-iqm | `git@github.com:openQSE/qhw-iqm.git` | `main` at `e3078979455188e1bda41ac25e280d92214a7d1c` | none | `e3078979455188e1bda41ac25e280d92214a7d1c` | `v0.1.0-rc.1` |
| qhw-admission | `git@github.com:openQSE/qhw-admission.git` | `main` at `fc106c340e74d4859dab30f9beb0cb7fea18dbb0` | integration source `fe5a4a9af3227dcb476ce8722239879a16387d90` | `47582500fcb1b06f3d32dad6aa78604dfbda67dd` | `v0.1.0-rc.1` |
| qhw-scheduler | `git@github.com:openQSE/qhw-scheduler.git` | `main` at `e5712bbce577af162d6a5bfdfb594de0db7a47eb` | integration source `5e303663d620f371a7a0d7d31f7e5feb2543470d` | `8cf431d6d64a844dbe04646711f566f7c36572bc` | `v0.1.0-rc.1` |
| DEFw | `git@github.com:openQSE/DEFw.git` | `master` at `ff9a8917c19f931a3dd866a633f18b487d04932e` | `de9d570d1d6b1393008c8d734783c0736e09c621` | `7728f89673efb96391e6880139ecccc8ce324f1b` | `v0.1.0-rc.1` |
| QFw | `git@github.com:openQSE/QFw.git` | `main` at `87895b715b421863b8eaf5635b4dc7ce1c3a72e2` | `ca383b13e0909686c03167a21a3d1c8963d8a633` | code tip `f45fcd8772a759abd8feab933eef8ea2a837ffbc`; final metadata commit as described above | `v0.1.0-rc.1` |

qhw-admission and qhw-scheduler did not have authoritative GitHub feature
branches at inventory time. Their exact feature commits were fetched from the
reviewed sibling repositories and preserved as merge parents. Their validated
release tips are published to the authoritative GitHub release branches.

## QFw gitlinks

The validated QFw source records this exact dependency set:

| Path | Commit |
| --- | --- |
| `DEFw` | `7728f89673efb96391e6880139ecccc8ce324f1b` |
| `external/qhw-data` | `63a24c88739a35bdafab3ef2cea88908f0845fb3` |
| `external/qhw-iqm` | `e3078979455188e1bda41ac25e280d92214a7d1c` |
| `external/qhw-admission` | `47582500fcb1b06f3d32dad6aa78604dfbda67dd` |
| `external/qhw-scheduler` | `8cf431d6d64a844dbe04646711f566f7c36572bc` |

The admission and scheduler repositories recursively select
qhw-datastructures commit `c01bcb3b1d561a393a48fb0257bedadcad8c2c2f`.

## Component validation

| Component | Validation result |
| --- | --- |
| qhw-data | Wheel and sdist built; isolated install, all schemas, bundled examples, JSON, and MessagePack passed. |
| qhw-iqm | Wheel and sdist built against selected qhw-data; four CLIs and four representative normalizers passed. |
| qhw-admission | 15/15 full and 6/6 no-plugin CTests; wheel, native context, package, and install tests passed. |
| qhw-scheduler | 23/23 normal and 23/23 static-enabled CTests; pure static build, 65,536 tasks under 13 policies, wheel, plugin, and install tests passed. |
| DEFw | 13/13 standalone CTests; TCP transport, RPC, directory, peers, SWIG, runner, and install tests passed. |
| QFw | Syntax/flake8 passed; 192 mock tests passed with one skip; 28 runtime tests and one Qiskit test passed; 15/15 bundled CTests passed. |

## Complete-stack validation

- A fresh recursive clone of the remote QFw release branch reconstructed all
  top-level and nested gitlinks.
- Source and installed runtime isolation passed. The installed prefix contains
  no development credential configuration or `qpu_users.json`.
- The standard Doug-cluster example matrix passed all ten entries: init, MPI,
  shim, Qiskit simple and GHZ, PennyLane GHZ and basic, QAOA, VQE, and
  SupermarQ.
- The allocated long-running NWQSim QPM test passed with two applications in
  each of two waves and clean teardown.
- Fake-IQM startup, smoke, admission, scheduler, and two-wave hybrid scenario
  sets passed with capacity restoration and no leaks.
- Real-IQM telemetry and owner credential preflight passed. The chemistry
  workflow completed through QFw with 21 IQM tasks, driver status `ok`, return
  code zero, accepted reservation release, and clean service teardown.
- Before and after installation and hardware testing, the protected credential
  path remained a root-owned regular file with mode `0600`; its metadata,
  size, and checksum were unchanged. No credential content is in this
  manifest or the retained shareable evidence.

## Tested environment

The complete stack was built and exercised in the
`qfw-slurm-cluster-doug` Docker Compose environment with the release-specific
Python 3.12 virtual environment and isolated source, build, and install
prefixes. Host-side clean-clone validation also used Python 3.10. Qiskit 2.2.3
was used for the Qiskit integration checks. The real chemistry test added
OpenFermion 1.8.1 to the dedicated test virtual environment.

## Publication checks still required

No `v0.1.0-rc.1` tag exists at the time of this manifest. Publication requires
explicit operator approval, re-fetching all refs, confirming these release
tips have not moved, fast-forwarding defaults without force, creating
annotated tags in dependency order, and verifying tag-triggered CI and
artifacts. QFw must be tagged last.
