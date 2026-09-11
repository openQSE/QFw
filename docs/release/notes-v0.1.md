# QFw v0.1 Release Notes

## Overview

QFw v0.1 aligns the release branch with the current mainline stack. The release
combines DEFw directory-service lifecycle work, QPM admission and scheduling,
Qiskit result handling, site-owned service operation, Slurm integration, and
the compact NWQSim statevector transport path.

The local release-sync state has `main` and `release/v0.1` pointing to the same
commit in QFw and the qhw repositories. DEFw uses `master`; local `master` and
`release/v0.1` point to the same commit there.

## Highlights

- QPM service discovery uses typed DEFw service records and QFw API-category
  bindings.
- QFw lifecycle handling ignores non-QPM service records when resolving QPM
  bindings.
- QPM execution carries admission, scheduler, reservation, completion, and
  telemetry state through the shared service path.
- Qiskit jobs can receive compact `base64+zlib` statevector payloads and build
  the expected Qiskit result objects on the application side.
- NWQSim services compress sparse statevectors before returning them through
  the QPM completion path.
- Example wrappers preserve application DEFw logs and use nanosecond run-log
  directory timestamps to avoid collisions.
- `qfw-srun` preserves caller module path variables so application-loaded
  modules, including OpenMPI for VQE, survive the runtime setup environment.
- The service catalog retains NWQSim, IQM, fake-IQM, shim, QB, TNQVM, QDMI,
  and QRMI service code.

## Component set

| Component | Commit |
| --- | --- |
| qhw-data | `63a24c88739a35bdafab3ef2cea88908f0845fb3` |
| qhw-iqm | `e3078979455188e1bda41ac25e280d92214a7d1c` |
| qhw-admission | `ab80d45d003beace53d09a673feaeb8f9633cb38` |
| qhw-scheduler | `51d6161ffaf197ad1a05a317bde6f228b1bfdcad` |
| DEFw | `f1033c2f79bd4abacff56b513894947acd90b1e4` |
| QFw | this release-notes commit |

QFw-SLURM-Cluster is not a QFw submodule. The validated local cluster sync used
commit `4787ff3f38d810651e2cc2f4bbe73c0dbf3e6f1f`.

## Validation summary

- DEFw, qhw-admission, and qhw-scheduler CMake builds and CTest suites passed.
- QFw mock and Qiskit tests passed.
- qfw-slurm CTest passed inside the merged image build.
- Dashboard tests passed.
- The merged SLURM cluster image installed QFw and qfw-slurm under
  `/opt/openqse` and configuration under `/etc/openqse`.
- Directory, gateway, NWQSim, IQM, and shim services started successfully.
- Runnable NWQSim examples passed from the installed examples directory.
- A 20-qubit qiskit-simple statevector run passed with a compact compressed
  result payload.

## Known limitations

- Upstream branches and tags are not updated by the local sync itself.
- QFw-SLURM-Cluster pulls QFw and qfw-slurm through configured refs during a
  normal Docker build; upstream `main` or `release/v0.1` must be updated before
  a default remote build can reproduce the local merged source state.
- Hardware-backed provider validation remains dependent on hardware and
  credential availability.
- Tag-triggered CI and release artifacts still need to run after upstream
  publication.
