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

### `qfw_chem_app.sh`

Runs a chemistry application script by name from
`examples/tests/chemistry_example_aim2`. Use this only when that
application tree is present.

```bash
./qfw_chem_app.sh <script-name.py>
```

### `qfw_supermarq.batch`

Frontier-oriented batch template for submitting the SupermarQ workflow
as a heterogeneous Slurm job. Update account, node counts, paths, and
arguments before use.
