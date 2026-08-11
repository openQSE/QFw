# QFw Examples

These scripts are intended to run after the QFw environment has been
activated inside a Slurm allocation. They are integration examples, not
unit tests.

```bash
source /opt/openqse/qfw/current/bin/qfw-activate
cd "$QFW_SHARE_DIR/examples"
```

Each wrapper starts QFw with `qfw-setup`, runs one application through
`qfw-srun`, and tears QFw down even when the application fails. Do not call
`qfw_deactivate` until the wrapper has completed.

Each wrapper and example emits machine-readable result records as JSON lines.
Records are printed with a `QFW_EXAMPLE_RESULT ` prefix in the log. When
`QFW_EXAMPLE_RESULT_FILE` is set, the same JSON records are appended to that
file. The stable fields are:

```text
schema: qfw-example-wrapper-v1 or qfw-example-result-v1
kind: wrapper or example
example: example name
status: ok, error, or running
timestamp_ns: record timestamp
parameters: example input parameters
metrics: example-specific measurements
artifacts: generated files, when any
```

## Quick Run

```bash
./qfw_init_test.sh
./qfw_mpi_smoke.sh
./qfw_shim_smoke.sh --lib qrmi
./qfw_qiskit_simple.sh 4
./qfw_ghz.sh qiskit 4 nwqsim 1
./qfw_pennylane.sh
./qfw_qaoa.sh nwqsim
./qfw_qiskit_vqe.sh 1
./qfw_supermarq.sh sync 1 4 128 false ghz nwqsim
```

To run the standard examples sequentially and collect per-example logs and
JSONL result files:

```bash
./qfw_run_all.sh
```

The runner continues after failures, prints a final summary, and exits
nonzero if any example fails. Logs, per-example JSONL files, and
`summary.jsonl` are written under
`$QFW_RUN_BASE_DIR/examples-run-<timestamp>`. If `QFW_RUN_BASE_DIR` is unset,
`qfw_run_all.sh` uses `${TMPDIR:-/tmp}/examples-run-<timestamp>`.

Useful overrides:

```bash
QFW_RUN_ALL_BACKEND=nwqsim ./qfw_run_all.sh
QFW_RUN_ALL_QUBITS=4 QFW_RUN_ALL_VQE_ITERS=1 ./qfw_run_all.sh
QFW_RUN_ALL_SHIM_LIB=qrmi ./qfw_run_all.sh
```

`qfw_run_all.sh` intentionally covers allocation-local managed examples. The
long-running QPM example below needs a multi-node allocation and is run
separately.

The fake IQM stress fixture is also run separately because it exercises a
bounded admission/scheduler matrix rather than a single application smoke.

## Example Wrappers

### `qfw_init_test.sh`

Validates that QFw can start and construct the configured Qiskit
backends.

```bash
./qfw_init_test.sh
```

### `qfw_mpi_smoke.sh`

Starts only the MPI smoke service from `qfw_mpi_smoke_services.yaml`,
runs the MPI-backed smoke API, and verifies rank/pid output.

```bash
./qfw_mpi_smoke.sh
```

Optional environment overrides:

```bash
QFW_MPI_SMOKE_NP=2 QFW_MPI_SMOKE_TIMEOUT=40 ./qfw_mpi_smoke.sh
```

### `qfw_shim_smoke.sh`

Starts only the QRMI/QDMI bifurcation shim service from
`qfw_shim_smoke_services.yaml`, resolves the shim QPM through DEFw-dirsvc, and
calls the shim service over DEFw RPC. The test covers device introspection,
coupling graph, calibration snapshot, backend info, async circuit execution,
completion notification, and last-job metadata.

```bash
./qfw_shim_smoke.sh --lib qdmi
./qfw_shim_smoke.sh --lib qrmi
./qfw_shim_smoke.sh --lib qdmi --call get_device_info
./qfw_shim_smoke.sh --lib qrmi --call async_run
```

`--lib` selects one shim library from the client for every call. To compare
libraries, `--libs` takes an ordered preference list and runs each
introspection call through those libraries in turn (a library that does not
serve a call is skipped), so you can see QDMI and QRMI results back-to-back:

```bash
./qfw_shim_smoke.sh --libs qdmi,qrmi
./qfw_shim_smoke.sh --libs qdmi,qrmi --call get_device_info
```

### `qfw_qiskit_simple.sh`

Runs a simple Qiskit GHZ-style circuit through the NWQ-Sim QFw backend.
The argument is the number of qubits.

```bash
./qfw_qiskit_simple.sh 4
```

### `qfw_ghz.sh`

Runs the GHZ example through either the Qiskit or PennyLane frontend.

Arguments:

```text
framework: qiskit or pennylane
num-qubits: number of qubits
simtype: nwqsim or tnqvm
iterations: number of repeated runs
```

Example:

```bash
./qfw_ghz.sh qiskit 4 nwqsim 4
```

### `qfw_pennylane.sh`

Runs the fixed PennyLane remote-backend example against the NWQ-Sim QFw
backend.

```bash
./qfw_pennylane.sh
```

### `qfw_qaoa.sh`

Runs the Qiskit QAOA Max-Cut example. The argument selects the simulator
backend.

```bash
./qfw_qaoa.sh nwqsim
./qfw_qaoa.sh tnqvm
```

### `qfw_qiskit_vqe.sh`

Runs the Qiskit VQE example against the NWQ-Sim statevector backend. The
argument is the maximum number of optimizer iterations.

```bash
./qfw_qiskit_vqe.sh 1
```

### `qfw_supermarq.sh`

Runs the SupermarQ example through QFw.

Arguments:

```text
run: sync or async
iterations: number of iterations
startqbit: starting qubit count
shots: number of shots
increase: true or false
method: ghz or vqe
backend: tnqvm, nwqsim, or qb
```

Example:

```bash
./qfw_supermarq.sh sync 1 4 128 false ghz nwqsim
```

### `qfw_fake_iqm_stress.sh`

Starts a deterministic fake `fake-iqm-20q` QPM and runs the
qhw-admission/qhw-scheduler stress driver. The wrapper uses only
`qfw-setup`, `qfw-srun`, and `qfw-teardown`.

```bash
./qfw_fake_iqm_stress.sh --scenario-set startup
./qfw_fake_iqm_stress.sh --scenario-set smoke --workers 2 --tasks-per-worker 2
./qfw_fake_iqm_stress.sh --scenario-set admission
./qfw_fake_iqm_stress.sh --scenario-set scheduler --workers 2 --tasks-per-worker 2
./qfw_fake_iqm_stress.sh --scenario-set hybrid --waves 2 --harness-walltime 120
```

Scenario records are printed with `QFW_FAKE_IQM_STRESS_RESULT ` and include
policy configuration, reservation decisions, capacity and scheduler snapshots,
worker submissions, completion records, timing metadata, release results, and
final leak checks.

### `qfw_chem_app.sh`

Runs a chemistry application script by name from
`examples/tests/chemistry_example_aim2`. Use this only when that
application tree is present.

```bash
./qfw_chem_app.sh <script-name.py>
```

### `qfw_iqm_chem_site_run.sh`

Starts a site-style DEFw directory service and ORNL IQM QPM, reserves through
the Slurm-style QFw driver, runs the QFw-enabled chemistry application, and
tears the site services down. The wrapper is intended for the Docker/site
workflow where the chemistry application tree and shared virtual environment
are visible to all nodes.

```bash
./qfw_iqm_chem_site_run.sh \
  --base /workspace/qfw-container-base \
  --qfw-prefix /workspace/qfw-container-base/qfw-install-dev \
  --venv /workspace/qfw-container-base/qfw-shared-test-venv \
  --device-access-config /workspace/qfw-container-base/qfw-install-dev/lib/qfw/services/dev-config/config.yaml \
  --chem-app-dir /workspace/qfw-container-base/chemistry_example_aim2
```

By default the wrapper uses `1000` shots and estimator precision `0.031623`,
which maps QFwEstimator submissions to `num_shots: 1000`. Override with
`--shots` and `--estimator-precision` when a different chemistry sampling
budget is needed.

### `qfw_supermarq.batch`

Frontier-oriented batch template for submitting the SupermarQ workflow
as a heterogeneous Slurm job. Update account, node counts, paths, and
arguments before use.

### `qfw_long_running_qpm.sh`

Runs a site-scoped long-running QPM workflow. The script expects at least
three allocated nodes by default. It starts a site DEFw-dirsvc, PRTE DVM, and
long-running `nwqsim` QPM on one service node, then launches concurrent
application waves on the remaining nodes. Each app uses site-scoped
`qfw-setup`, `qfw-srun`, and `qfw-teardown`; app teardown must not stop the
site service plane.

```bash
./qfw_long_running_qpm.sh --apps 2 --waves 2 --backend nwqsim
```

Useful overrides:

```bash
./qfw_long_running_qpm.sh --service-node c1 --app-nodes c2,c3
QFW_LONG_RUNNING_QPM_FORCE_PRTE_CLEANUP=yes ./qfw_long_running_qpm.sh
```

Logs, generated site/runtime configs, service PID files, per-app logs, and
`summary.jsonl` are written under
`$QFW_RUN_BASE_DIR/long-running-qpm-<timestamp>`. If `QFW_RUN_BASE_DIR` is
unset, the script uses `${TMPDIR:-/tmp}/qfw-runs`.

### `qfw_long_running_qpm.batch`

Three-node Slurm batch template for `qfw_long_running_qpm.sh`. Set
`QFW_ACTIVATE` when the QFw install is not under
`/opt/openqse/qfw/current/bin/qfw-activate`.
