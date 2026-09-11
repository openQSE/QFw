# QFw v0.1 Release Manifest

## Release identity

- Release branch: `release/v0.1`
- Mainline branch: `main`
- Planned release tag: `v0.1.0`
- Publication state: local branches aligned; upstream branches and tags are not
  updated by this manifest.

At the release-sync point, local `main` and local `release/v0.1` point to the
same QFw commit. The QFw commit containing this manifest is the release
metadata commit. The code and submodule state validated before the documentation
cleanup was `a4caac357425dd5391ad0fe22b8761e5034bea05`.

## Component set

| Component | Remote | Branch state | Selected commit |
| --- | --- | --- | --- |
| qhw-data | `git@github.com:openQSE/qhw-data.git` | `main == release/v0.1` | `63a24c88739a35bdafab3ef2cea88908f0845fb3` |
| qhw-iqm | `git@github.com:openQSE/qhw-iqm.git` | `main == release/v0.1` | `e3078979455188e1bda41ac25e280d92214a7d1c` |
| qhw-admission | `git@github.com:openQSE/qhw-admission.git` | `main == release/v0.1` | `ab80d45d003beace53d09a673feaeb8f9633cb38` |
| qhw-scheduler | `git@github.com:openQSE/qhw-scheduler.git` | `main == release/v0.1` | `51d6161ffaf197ad1a05a317bde6f228b1bfdcad` |
| DEFw | `git@github.com:openQSE/DEFw.git` | `master == release/v0.1` | `f1033c2f79bd4abacff56b513894947acd90b1e4` |
| QFw | `git@github.com:openQSE/QFw.git` | `main == release/v0.1` | this manifest commit |

The admission and scheduler repositories recursively select qhw-datastructures
commit `c01bcb3b1d561a393a48fb0257bedadcad8c2c2f`.

## QFw gitlinks

The QFw source records this dependency set:

| Path | Commit |
| --- | --- |
| `DEFw` | `f1033c2f79bd4abacff56b513894947acd90b1e4` |
| `external/qhw-data` | `63a24c88739a35bdafab3ef2cea88908f0845fb3` |
| `external/qhw-iqm` | `e3078979455188e1bda41ac25e280d92214a7d1c` |
| `external/qhw-admission` | `ab80d45d003beace53d09a673feaeb8f9633cb38` |
| `external/qhw-scheduler` | `51d6161ffaf197ad1a05a317bde6f228b1bfdcad` |

## Validation summary

| Component | Validation result |
| --- | --- |
| DEFw | CMake configure/build passed; 14/14 CTest tests passed; 3/3 Python tests passed with the runtime build paths on `PYTHONPATH`. |
| qhw-admission | CMake configure/build passed; 15/15 CTest tests passed. |
| qhw-scheduler | CMake configure/build passed; 23/23 CTest tests passed. |
| QFw | Mock tests passed with 280 passed and 1 skipped; Qiskit tests passed. |
| QFw-SLURM-Cluster | Merged stack image built and installed to official paths; qfw-slurm image-build CTest reported 11/11 passed. |
| Dashboard | Dashboard tests passed with 93 passed. |

Complete-stack validation used the local QFw-SLURM-Cluster sync at
`4787ff3f38d810651e2cc2f4bbe73c0dbf3e6f1f` and image
`qfw-slurm-cluster:main-sync`
`sha256:2b5c2bcdd9b7bed81fbe80f0851435642084e060d584a499e0706a3876511893`.

## Complete-stack validation

- The cluster started from the merged image and installed QFw and qfw-slurm
  under `/opt/openqse`.
- Site configuration resolved from `/etc/openqse`.
- Python imports resolved from `/opt/openqse/qfw` and
  `/opt/openqse/qfw-venv`, not from the source workspace.
- `qfw-site-services start --target all` completed within one minute.
- Directory, gateway, NWQSim, IQM, and shim services reported `up`.
- `qfw-sinfo` listed NWQSim, IQM, and shim targets as idle.
- `qfw-squeue` returned an empty queue header.
- Runnable NWQSim examples passed: init, qiskit-simple, GHZ with Qiskit, GHZ
  with PennyLane, PennyLane, QAOA, VQE, and SuperMarQ.
- A 20-qubit qiskit-simple run returned counts and a statevector. The
  statevector payload compressed from 16,777,216 raw bytes to 16,353 zlib bytes
  and 21,804 base64 bytes.

## Publication checks still required

Before upstream publication, fetch all remotes, confirm the local branch tips
still match the intended release state, push child repositories first, update
QFw after child refs are available, then update QFw-SLURM-Cluster. Create the
release tag only after the pushed branch tips and validation artifacts are
confirmed.
