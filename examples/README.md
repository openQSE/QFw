# QFw Examples

These scripts are intended to run after the QFw environment has been
activated inside a Slurm allocation. They are integration examples, not
unit tests.

```bash
source /path/to/QFw/setup/qfw_activate
cd "$QFW_PATH/examples"
```

Each wrapper starts QFw with `qfw_setup.sh`, runs one application through
`qfw_srun.sh`, and then calls `qfw_teardown.sh`. Do not call
`qfw_deactivate` until the wrapper has completed.

## Quick Run

```bash
./qfw_init_test.sh
./qfw_mpi_smoke.sh
./qfw_qiskit_simple.sh 4
./qfw_ghz.sh qiskit 4 nwqsim 1
./qfw_pennylane.sh
./qfw_qaoa.sh nwqsim
./qfw_qiskit_vqe.sh 1
./qfw_supermarq.sh sync 1 4 128 false ghz nwqsim
```

To run the standard examples sequentially and collect per-example logs:

```bash
./qfw_run_all.sh
```

The runner continues after failures, prints a final summary, and exits
nonzero if any example fails. Logs are written under
`$QFW_TMP_PATH/examples-run-<timestamp>` or `/tmp` when `QFW_TMP_PATH` is
unset.

Useful overrides:

```bash
QFW_RUN_ALL_BACKEND=nwqsim ./qfw_run_all.sh
QFW_RUN_ALL_QUBITS=4 QFW_RUN_ALL_VQE_ITERS=1 ./qfw_run_all.sh
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
