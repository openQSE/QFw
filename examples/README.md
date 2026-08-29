# QFw Examples

These scripts are intended to run after the QFw environment has been
activated inside a Slurm allocation. They are integration examples, not
unit tests. Run `man 7 qfw-examples` for the installed overview and
`man 1 <script-name>` for a public wrapper's complete command reference.

```bash
source /opt/openqse/qfw/current/bin/qfw-activate
cd "$QFW_SHARE_DIR/examples"
```

Each compatible wrapper accepts `--service-mode local|site` and `--backend`.
Local mode selects the installed `local` profile and starts only the requested
application-owned backend. Site mode uses the installed default site-only
runtime and resolves an existing site-owned QPM. Neither mode requires an
application-generated runtime file. Both modes run through `qfw-srun` and call
`qfw-teardown` even when the application fails. Do not call `qfw-deactivate`
until the wrapper has completed.

Example scripts are quiet by default. Pass `--verbose` before the wrapper's
positional arguments to enable shell command tracing:

```bash
./qfw_ghz.sh --verbose qiskit 4 nwqsim 1
```

Each wrapper and example emits a result record. Standard output shows a
human-readable, pretty-printed JSON block whose opening line starts with
`QFW_EXAMPLE_RESULT `. When `QFW_EXAMPLE_RESULT_FILE` is set, the same record
is appended to that file as compact, one-record-per-line JSONL for machine
consumption. The stable fields are:

```text
QFW_EXAMPLE_RESULT {
  "artifacts": {},
  "details": {},
  "example": "ghz-qiskit",
  ...
}
```

Other structured records emitted by the example drivers use the same
pretty-printed stdout format, including `QFW_SLURM_DRIVER_RESULT`,
`QFW_EXAMPLE_RESERVATION`, `QFW_FAKE_IQM_STRESS_RESULT`, and chemistry and IQM
result prefixes. Files whose names end in `.jsonl` remain compact JSONL and
should be used by automation instead of parsing terminal output.

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

Run the compatible examples locally against NWQSim:

```bash
./qfw_run_all.sh --service-mode local --backend nwqsim
```

Run them against an existing site-owned QPM:

```bash
./qfw_run_all.sh \
  --service-mode site \
  --backend nwqsim
```

The site configuration comes from the activated `QFW_SITE_CONFIG`. Pass
`--site-config` only to override it for that invocation. `--runtime-config` is
also an advanced override, not a requirement for site mode.

The runner continues after failures, prints a final summary, and exits
nonzero if any example fails. Logs, per-example JSONL files, and
`summary.jsonl` are written under
`$QFW_RUN_BASE_DIR/examples-run-<timestamp>`. If `QFW_RUN_BASE_DIR` is unset,
`qfw_run_all.sh` uses `${TMPDIR:-/tmp}/examples-run-<timestamp>`.

Useful overrides:

```bash
./qfw_run_all.sh --tests init-test,qiskit-simple,ghz-qiskit
QFW_RUN_ALL_QUBITS=4 QFW_RUN_ALL_VQE_ITERS=1 ./qfw_run_all.sh
QFW_RUN_ALL_SHIM_LIB=qrmi ./qfw_run_all.sh
```

The aggregate runner intentionally excludes `qfw_mpi_smoke.sh`; MPI validation
has its own placement and task-count contract and remains a separate command.
The shim smoke test runs only in local mode because it owns a specialized
service. VQE is skipped for backends without the required statevector result.
Every selected case must emit a successful terminal wrapper record to pass.

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

This specialized case is not part of a simulator-backed `qfw_run_all.sh`
matrix. Select it explicitly with `--service-mode local --backend shim`.

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

### `measure_shim_introspection.py`

Measures what device introspection costs through each shim library, cold and
warm, for the QRMI/QDMI comparison. Read-only: no circuits, no QPU time.

Unlike the wrappers above this is a standalone script. It drives the shim
drivers directly rather than going through the QPM service, so DEFw RPC does
not sit in the measurement, and it needs no Slurm allocation — QRMI's
`target()` is not reservation-bound and QDMI needs only an initialized session.
Activate the environment and run it:

```bash
python measure_shim_introspection.py
python measure_shim_introspection.py --repeat 5 --warm-iterations 10
python measure_shim_introspection.py --json
```

The two libraries pay their network cost at different moments — QRMI on the
first call (`target()`, then cached per driver instance), QDMI at open (session
init fetches the device data) — so the report separates open, first call, and
warm calls, and compares the cold totals. Each cold sample runs in a fresh
subprocess, because both drivers cache on the instance and the FoMaC loader
registers its device library process-wide.

The script refuses to report a timing whose call returned no qubits. QRMI's
`target()` does not raise when its fetches fail; it substitutes nulls, so an
unreachable endpoint otherwise yields believable numbers that measure only the
speed of failing.

### `qfw_qiskit_simple.sh`

Runs a simple Qiskit GHZ-style circuit through the selected QFw backend. The
argument is the number of qubits.

```bash
./qfw_qiskit_simple.sh 4
```

### `qfw_ghz.sh`

Runs the GHZ example through either the Qiskit or PennyLane frontend.

Arguments:

```text
framework: qiskit or pennylane
num-qubits: number of qubits
backend: QFw provider backend name
iterations: number of repeated runs
```

Example:

```bash
./qfw_ghz.sh qiskit 4 nwqsim 4
```

### `qfw_pennylane.sh`

Runs the fixed PennyLane remote-backend example against the selected QFw
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

Use the common execution options to select a site-owned hardware QPM:

```bash
./qfw_chem_app.sh \
  --service-mode site \
  --backend iqm \
  <script-name.py>
```

### `qfw_iqm_chem_driver.sh`

The driver performs credential preflight, reserves through the Slurm-style
driver, runs the chemistry application against an existing IQM QPM, and
records evidence. Pass the canonical site file directly with `--site-config`.
The command does not source state from a service manager run directory.

Long-running site-service startup, application validation, and interruption
recovery are documented in `docs/recipes`.
