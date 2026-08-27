# QFw Recipes

These recipes provide task-oriented procedures for installing QFw and running
it in local, normal Slurm, and heterogeneous Slurm environments. Start with the
Docker or installation recipe that matches the target system, then choose an
execution recipe.

## Recipes

| Goal | Recipe |
| --- | --- |
| Configure the QFw Docker/Slurm development environment | [Docker Slurm environment](docker-slurm-environment.md) |
| Install into an arbitrary user or development prefix | [Non-standard installation](install-nonstandard-location.md) |
| Install into the conventional site prefix | [Standard site installation](install-standard-location.md) |
| Build optional NWQSim or TNQVM dependencies | [Simulator dependencies](build-simulator-dependencies.md) |
| Prepare a reusable, site-owned QPM | [Site-owned QPM configuration](configure-long-running-qpm.md) |
| Exercise a site-owned QPM from a normal allocation | [Site QPM, normal allocation](test-long-running-qpm-normal.md) |
| Exercise a site-owned QPM from a heterogeneous allocation | [Site QPM, heterogeneous allocation](test-long-running-qpm-heterogeneous.md) |
| Run an application and its services on one node | [Same-node execution](test-same-node.md) |
| Separate the application and services with a heterogeneous allocation | [Heterogeneous execution](test-heterogeneous-allocation.md) |
| Recover interrupted directory, DVM, QPM, or reservation state | [Service recovery](recover-services.md) |

## Command and Configuration References

Recipes describe complete workflows. When a QFw command first appears, the
recipe names its manual page and gives the corresponding `man` command. Command
options and generated state belong in section 1 manuals. Configuration file
fields belong in section 5 manuals, while lifecycle concepts belong in section
7.

For example:

```bash
man 1 qfw-setup
man 5 qfw-site.yaml
man 7 qfw-service-lifecycle
```

Example scripts under `share/qfw/examples` are workflow inputs rather than
installed commands. Use their `--help` option when available for
script-specific arguments.

## Shared Installation and Runtime Conventions

The Docker Slurm cluster mounts one host directory into every container at
`/workspace/qfw-container-base`. Installation recipes use it as the common
root for QFw source, builds, installations, and Python environments:

```bash
export QFW_SHARED_ROOT=/workspace/qfw-container-base
```

Application and service-launch recipes select runtime storage when a run
begins:

```bash
export QFW_RUN_BASE_DIR="${QFW_SHARED_ROOT}/qfw-runs"
mkdir -p "${QFW_RUN_BASE_DIR}"
```

`QFW_SHARED_ROOT`, `QFW_SRC`, `QFW_BUILD`, `QFW_VENV`, and
`QFW_INSTALL_PREFIX` are shell conveniences used by these recipes.
`QFW_RUN_BASE_DIR` is the QFw runtime setting consumed by QFw commands.

`QFW_RUN_BASE_DIR` is not an installation setting. Use a unique directory
below it for each concurrently active runtime. The QFw commands create a
unique run automatically unless a recipe passes `--run-dir` explicitly.

> **Shared-filesystem requirement:** For heterogeneous or multinode execution,
> `QFW_RUN_BASE_DIR` must be writable and visible at the same pathname on every
> participating node. QFw starts PRTE with `--report-uri` and later passes the
> generated `QFW_DVM_URI_PATH` to MPI as a file URI. Node-local `/tmp` is not a
> valid run base for these executions.

The QFw prefix and Python virtual environment must also be accessible at the
same paths on every participating node. They may reside on shared storage or be
deployed identically to each node.

## Runtime Choices

QPM lifetime and Slurm placement are separate choices:

| QPM lifetime | Placement | Recipe |
| --- | --- | --- |
| Allocation-owned | Application and QPM on one node | [Same-node execution](test-same-node.md) |
| Allocation-owned | Application in group 0, QPM in group 1 | [Heterogeneous execution](test-heterogeneous-allocation.md) |
| Site-owned and reused by applications | Normal multinode allocation | [Site QPM, normal allocation](test-long-running-qpm-normal.md) |
| Site-owned and reused by applications | Heterogeneous allocation | [Site QPM, heterogeneous allocation](test-long-running-qpm-heterogeneous.md) |

Site services are always owned through independent `qfw-dir-svc(1)` and
`qfw-qpm-svc(1)` instances. Run `man 1 qfw-dir-svc` and
`man 1 qfw-qpm-svc` for their lifecycle contracts. The example runner's
`--service-mode site` option connects each compatible case to those existing
services without transferring ownership or requiring an application-generated
runtime file. `--service-mode local` selects the installed local profile and
only the requested backend service. Run `man 1 qfw_run_all.sh` for runner
arguments and examples, and `man 1 qfw-setup` for profile selection details.

<details>
<summary>Diagnostics, verification, reservations, and cleanup</summary>

After activation, inspect the selected installation and Python environment:

```bash
printf 'QFW_PREFIX=%s\n' "${QFW_PREFIX:-unset}"
printf 'VIRTUAL_ENV=%s\n' "${VIRTUAL_ENV:-unset}"
printf 'QFW_RUN_BASE_DIR=%s\n' "${QFW_RUN_BASE_DIR:-unset}"
python -c 'import sys; print("python=", sys.executable); print(*sys.path, sep="\n")'
```

Runtime state, readiness files, logs, and reservation records are kept below
`QFW_RUN_BASE_DIR`. `qfw-status(1)` reports that state; run
`man 1 qfw-status` for its output contract and selection rules. Useful checks
are:

```bash
qfw-status
find "${QFW_RUN_BASE_DIR}" -type f \
  \( -name '*ready*.json' -o -name 'runtime-state.json' \
     -o -name 'summary.jsonl' \) -print
grep -R 'QFW_EXAMPLE_RESERVATION' "${QFW_RUN_BASE_DIR}" 2>/dev/null || true
squeue -u "${USER}"
```

Example wrappers in local mode perform their own application teardown. For a
runtime prepared manually, use its explicit run directory. The cleanup
commands are documented by `qfw-teardown(1)` and `qfw-deactivate(1)`; run
`man 1 qfw-teardown` and `man 1 qfw-deactivate` for details.

```bash
qfw-teardown --run-dir /shared/path/to/the/run
qfw-deactivate
```

When activation used `--venv`, `qfw-deactivate` also deactivates that virtual
environment and restores the Python environment that preceded QFw activation.

Do not use application-side `qfw-teardown` to stop an operator-owned site QPM.
Stop site services through the service manager or operator procedure that
started them.

</details>
