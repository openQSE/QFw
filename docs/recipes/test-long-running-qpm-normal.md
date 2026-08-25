# Test a Site-owned NWQSim QPM from a Normal Allocation

This recipe runs the compatible examples from a normal application allocation
against an NWQSim QPM that the site administrator already operates. The
directory, DVM, and QPM remain outside the application allocation.

## 1. Confirm the site-service prerequisite

The administrator must complete [Configure a site-owned
QPM](configure-long-running-qpm.md) before an application job starts. Its
directory connection file must be readable from the application nodes.

The application needs the client-readable site configuration. It does not need
the QPM manager run directory or DVM URI.

`qfw-activate(1)` prepares the application shell; run `man 1 qfw-activate` for
its environment and virtual-environment rules. Run `man 1 qfw_run_all.sh` for
the example runner's arguments and runnable examples.

## 2. Request and prepare the application allocation

```bash
salloc --nodes=1 --ntasks=1 --time=01:00:00

export QFW_SHARED_ROOT=/shared/openqse/qfw
export QFW_RUN_BASE_DIR="${QFW_SHARED_ROOT}/application-runs"
export QFW_INSTALL_PREFIX=/opt/openqse/qfw/current
export QFW_VENV=/opt/openqse/qfw/venv
export QFW_SITE_CONFIG=/etc/openqse/qfw/site.yaml

source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" --venv "${QFW_VENV}"
mkdir -p "${QFW_RUN_BASE_DIR}"
```

No application-specific runtime file is needed. Site service mode uses the
installed default runtime, whose resolver is site-only and whose configuration
contains no application-owned services. See `qfw-runtime.yaml(5)` with
`man 5 qfw-runtime.yaml`.

## 3. Run the examples

```bash
cd "${QFW_SHARE_DIR}/examples"
./qfw_run_all.sh \
  --service-mode site \
  --backend nwqsim
```

Each compatible example passes the activated `QFW_SITE_CONFIG` to
`qfw-setup(1)` without selecting a profile. An application developer can use
the same behavior by passing `--service-mode site` to an individual wrapper,
such as `qfw_ghz.sh`. Run `man 1 qfw_ghz.sh` for that test,
`man 1 qfw_run_all.sh` for runner options, and `man 1 qfw-setup` for runtime
selection details.

Every passing case must emit a successful terminal JSONL record. MPI smoke
remains separate because it has its own task-placement contract.

The administrator can compare `qfw-qpm-svc status` before and after this test
to confirm that the QPM instance did not restart. `qfw-qpm-svc(1)` documents
the status output; run `man 1 qfw-qpm-svc` for details. Application teardown
cannot stop the site-owned service.

`qfw-deactivate(1)` restores the shell after the test. Run
`man 1 qfw-deactivate` for its cleanup behavior.

<details>
<summary>Diagnostics, results, and cleanup</summary>

```bash
find "${QFW_RUN_BASE_DIR}" -type f \
  \( -name 'summary.jsonl' -o -name '*.log' -o -name 'runtime-state.json' \) \
  -print
squeue -j "${SLURM_JOB_ID}"
qfw-deactivate
```

Use [Service recovery](recover-services.md) when the site QPM is unavailable.
Only the site administrator should operate its manager run directory.

</details>
