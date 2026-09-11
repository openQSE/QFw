# QFw v0.1 Source-Change Report

## Scope

This report summarizes the local release/main synchronization for QFw v0.1.
The goal was to preserve release behavior, preserve newer mainline behavior,
and leave local `main` and local `release/v0.1` at the same commit.

The coordinated QFw source set includes QFw, DEFw, qhw-data, qhw-iqm,
qhw-admission, qhw-scheduler, and qhw-characterization. The validated stack also
uses qfw-slurm and QFw-SLURM-Cluster, but QFw-SLURM-Cluster pulls QFw and
qfw-slurm by build ref rather than through QFw gitlinks.

## Branch alignment

| Repository | Mainline branch | Final local state |
| --- | --- | --- |
| DEFw | `master` | `master == release/v0.1` at `f1033c2f79bd4abacff56b513894947acd90b1e4` |
| QFw | `main` | `main == release/v0.1` at the commit containing this report |
| qfw-slurm | `main` | `main == release/v0.1` at `0c3694bb82a3413a4aaee3b2944db1b4dd346beb` |
| qhw-admission | `main` | `main == release/v0.1` at `ab80d45d003beace53d09a673feaeb8f9633cb38` |
| qhw-data | `main` | `main == release/v0.1` at `63a24c88739a35bdafab3ef2cea88908f0845fb3` |
| qhw-iqm | `main` | `main == release/v0.1` at `e3078979455188e1bda41ac25e280d92214a7d1c` |
| qhw-scheduler | `main` | `main == release/v0.1` at `51d6161ffaf197ad1a05a317bde6f228b1bfdcad` |
| qhw-characterization | `main` | `main == release/v0.1` at `a86793b3cd0956d4d8de450353fb93e26fd9bf4bc` |

## Merge summary

- DEFw had 52 release-only commits and no mainline-only commits. The merge was
  clean and produced `f1033c2`.
- qhw-admission had 6 release-only commits and no mainline-only commits. The
  merge was clean and produced `ab80d45`.
- qhw-scheduler had 4 release-only commits and no mainline-only commits. The
  merge was clean and produced `51d6161`.
- qhw-data, qhw-iqm, qhw-characterization, and qfw-slurm already had matching
  local mainline and release states.
- QFw had substantial two-sided history: 104 release-only commits and 33
  mainline-only commits before the merge. Conflicts were resolved file by file.
- QFw-SLURM-Cluster had substantial two-sided history: 53 release-only commits
  and 19 mainline-only commits before the merge. Conflicts were resolved file
  by file.

## QFw conflict resolutions

- `.gitmodules` now tracks mainline branch names after sync: DEFw uses
  `master`; qhw submodules use `main`.
- The QDMI driver keeps the mainline `mqt.core.qdmi.driver` import path while
  retaining release-side QDMI execution behavior.
- Device-access normalization forwards shim descriptor fields and preserves the
  normalized `execution_owner` key.
- Mock-test fixtures preserve both the DEFw service logging stub and the
  reusable example-script fixture.
- The IQM chemistry driver test conflict was converted into shared helpers and
  explicit test cases.
- The IQM chemistry example preserved `--service-run-dir` support while
  retaining release option parsing and reservation behavior.

## Follow-up fixes from validation

- VQE needed OpenMPI available before `mpi4py` imports. The example now loads
  OpenMPI before launching the application path.
- `qfw-srun` now preserves caller path-style environment variables so module
  loads are not clobbered by setup-state restoration.
- QFw-SLURM-Cluster provisioning now includes the `shim-head` node so shim has
  the same `/etc/openqse/qfw` site configuration as the other service nodes.
- The dashboard service helper now detaches with `setsid` and redirects stdin
  from `/dev/null` so the service remains alive after helper startup.

## Validation result

The final local sync passed unit, component, image, service, dashboard, and
installed-example validation. Runnable NWQSim examples passed from the
installed examples directory, and the 20-qubit qiskit-simple case verified the
compact statevector path.

No upstream branches or tags were pushed as part of this local sync.
