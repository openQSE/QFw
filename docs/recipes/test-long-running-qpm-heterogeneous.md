# Test a Site-owned NWQSim QPM from a Heterogeneous Allocation

This recipe validates application placement in a heterogeneous allocation
while using an existing site-owned NWQSim QPM. The directory, DVM, and QPM run
outside both heterogeneous groups.

## 1. Confirm the site-service prerequisite

The administrator must complete [Configure a site-owned
QPM](configure-long-running-qpm.md). Both application groups need access to the
client-readable site configuration and its directory connection file. They do
not need the site QPM run directory or DVM URI.

`qfw-activate(1)` prepares the application shell; run `man 1 qfw-activate` for
its environment and virtual-environment rules. The example runner documents
its arguments through `qfw_run_all.sh --help`.

## 2. Request the application allocation

```bash
salloc --nodes=1 --ntasks=1 --time=01:00:00 \
  : --nodes=1 --ntasks=1 --time=01:00:00

export QFW_SHARED_ROOT=/shared/openqse/qfw
export QFW_RUN_BASE_DIR="${QFW_SHARED_ROOT}/application-runs"
export QFW_INSTALL_PREFIX=/opt/openqse/qfw/current
export QFW_VENV=/opt/openqse/qfw/venv
export QFW_SITE_CONFIG=/etc/openqse/qfw/site.yaml

source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" --venv "${QFW_VENV}"
mkdir -p "${QFW_RUN_BASE_DIR}"
```

No application-specific runtime file is needed. Site service mode uses the
installed default site-only runtime. See `qfw-runtime.yaml(5)` with
`man 5 qfw-runtime.yaml`.

## 3. Run against the existing QPM

```bash
cd "${QFW_SHARE_DIR}/examples"
./qfw_run_all.sh \
  --service-mode site \
  --backend nwqsim
```

Each compatible example passes the activated `QFW_SITE_CONFIG` to
`qfw-setup(1)` without selecting a profile. The same behavior applies when an
individual wrapper, such as `qfw_ghz.sh`, receives `--service-mode site`. Run
`qfw_run_all.sh --help` for runner options and `man 1 qfw-setup` for runtime
selection details.

`qfw-srun(1)` places ordinary application work in group 0. Run
`man 1 qfw-srun` for its placement options. Group 1 remains
available for application workflows that explicitly need a second placement
group. Neither group owns the site QPM or its DVM.

`qfw-deactivate(1)` restores the shell after the test. Run
`man 1 qfw-deactivate` for its cleanup behavior.

<details>
<summary>Diagnostics, results, and cleanup</summary>

```bash
printf 'group0=%s\ngroup1=%s\n' \
  "${SLURM_JOB_NODELIST_HET_GROUP_0}" \
  "${SLURM_JOB_NODELIST_HET_GROUP_1}"
squeue -j "${SLURM_JOB_ID}" -o '%.18i %.9P %.8T %.20N'
find "${QFW_RUN_BASE_DIR}" -type f \
  \( -name 'summary.jsonl' -o -name '*.log' -o -name 'runtime-state.json' \) \
  -print
qfw-deactivate
```

Use [Service recovery](recover-services.md) when the site service is
unavailable. Application cleanup remains limited to application-owned state.

</details>
