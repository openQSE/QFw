# Run Application-owned NWQSim Tests in a Heterogeneous Allocation

Applications run in group 0 while their application-owned directory, DVM, and
QPM run in group 1.

`qfw-activate(1)` prepares the shell; run `man 1 qfw-activate` for its
environment and virtual-environment rules. `qfw-deactivate(1)` restores the
shell and deactivates the virtual environment selected by `--venv`; run
`man 1 qfw-deactivate` for details. The example runner documents its arguments
through `qfw_run_all.sh --help`.

```bash
salloc --nodes=1 --ntasks=1 --time=00:45:00 \
  : --nodes=1 --ntasks=1 --time=00:45:00

export QFW_SHARED_ROOT=/workspace/qfw-container-base
export QFW_RUN_BASE_DIR="${QFW_SHARED_ROOT}/qfw-runs"
export QFW_INSTALL_PREFIX="${QFW_SHARED_ROOT}/qfw-release-v0.1-install"
export QFW_VENV="${QFW_SHARED_ROOT}/qfw-release-v0.1-venv"
mkdir -p "${QFW_RUN_BASE_DIR}"

source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" --venv "${QFW_VENV}"
cd "${QFW_SHARE_DIR}/examples"

./qfw_run_all.sh --service-mode local --backend nwqsim
./qfw_mpi_smoke.sh

qfw-deactivate
```

Both groups must see `QFW_RUN_BASE_DIR` at the same pathname because group 1
writes the PRTE DVM URI there. Each example owns and tears down only its own
runtime. Local service mode selects the installed `local` runtime profile and
starts only the backend requested by that example. It does not generate an
application-specific runtime file. See `qfw-setup(1)` with `man 1 qfw-setup`
and `qfw-runtime.yaml(5)` with `man 5 qfw-runtime.yaml`.

<details>
<summary>Diagnostics and results</summary>

```bash
printf 'group0=%s\ngroup1=%s\n' \
  "${SLURM_JOB_NODELIST_HET_GROUP_0}" \
  "${SLURM_JOB_NODELIST_HET_GROUP_1}"
squeue -j "${SLURM_JOB_ID}" -o '%.18i %.9P %.8T %.20N'
find "${QFW_RUN_BASE_DIR}" -type f \
  \( -name 'summary.jsonl' -o -name '*.log' -o -name 'dvm-uri' \) -print
```

</details>
