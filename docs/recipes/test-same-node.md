# Run NWQSim Tests on One Node

Use application-owned service mode for the simplest QFw validation topology.

`qfw-activate(1)` prepares the shell; run `man 1 qfw-activate` for its
environment and virtual-environment rules. `qfw-deactivate(1)` restores the
shell and deactivates the virtual environment selected by `--venv`; run
`man 1 qfw-deactivate` for details. The example runner documents its arguments
through `qfw_run_all.sh --help`.

```bash
salloc --nodes=1 --ntasks=1 --time=00:30:00

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

Each backend example creates and tears down its own application runtime. MPI
smoke is intentionally separate because it tests an MPI service rather than a
quantum backend. Local service mode selects the installed `local` runtime
profile and starts only the backend requested by that example. It does not
generate an application-specific runtime file. See `qfw-setup(1)` with
`man 1 qfw-setup` and `qfw-runtime.yaml(5)` with `man 5 qfw-runtime.yaml`.

<details>
<summary>Diagnostics and results</summary>

```bash
printf 'QFW_PREFIX=%s\nVIRTUAL_ENV=%s\nQFW_RUN_BASE_DIR=%s\n' \
  "${QFW_PREFIX}" "${VIRTUAL_ENV}" "${QFW_RUN_BASE_DIR}"
find "${QFW_RUN_BASE_DIR}" -type f \
  \( -name 'summary.jsonl' -o -name '*.log' -o -name 'runtime-state.json' \) \
  -print
squeue -j "${SLURM_JOB_ID}"
```

</details>
