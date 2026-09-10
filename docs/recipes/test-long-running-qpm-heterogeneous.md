# Test a Site-owned NWQSim QPM from a Heterogeneous Allocation

This recipe validates one canonical QPM reservation across a two-component
Slurm heterogeneous allocation. The directory, gateway, DVM, and QPM remain
site-owned and outside both application components.

## 1. Confirm prerequisites

Complete [Configure a site-owned QPM](configure-long-running-qpm.md) and
[Configure the Docker Slurm environment](docker-slurm-environment.md). Enter
the controller as a regular user:

```bash
cd /path/to/QFw-SLURM-Cluster
./do_ssh.sh --user user-a
```

## 2. Allocate both components

Place the quantum options on the component that runs the QFw application. In
this example that is heterogeneous group 0. Run `man salloc` for heterogeneous
syntax and `man 7 qfw-slurm` for the installed qfw-slurm lifecycle.

```bash
salloc --partition=normal --nodes=1 --ntasks=1 --time=00:30:00 \
  --qpu=nwqsim \
  --workload-kind=hybrid \
  --circ-count=4 \
  --max-qubits=5 \
  --max-depth=100 \
  --max-shots=1024 \
  : --partition=normal --nodes=1 --ntasks=1 --time=00:30:00
```

The gateway verifies either observed component job ID against Slurm and maps
it to the canonical heterogeneous allocation. Consequently, application steps
in either component receive the same reservation tuple set.

## 3. Run the application in group 0

Run `man 1 qfw-activate`, `man 1 qfw-setup`, `man 1 qfw-srun`,
`man 1 qfw-teardown`, and `man 1 qfw-deactivate` for command details.

```bash
source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" \
  --venv "${QFW_VENV}"

cd "${QFW_SHARE_DIR}/examples"
qfw-setup
qfw-srun --het-group 0 tests/test_qiskit_simple.py 5 nwqsim
qfw-teardown
qfw-deactivate
```

The example must emit a terminal JSON record with `"status": "ok"`.

<details>
<summary>Component and reservation checks</summary>

```bash
srun --het-group=0 /usr/bin/env | grep '^QFW_RESERVATIONS='
srun --het-group=1 /usr/bin/env | grep '^QFW_RESERVATIONS='
printf 'group0=%s\ngroup1=%s\n' \
  "${SLURM_JOB_NODELIST_HET_GROUP_0}" \
  "${SLURM_JOB_NODELIST_HET_GROUP_1}"
qfw-status
```

Both environment checks must print identical compact JSON. Remote SPANK gets
that value from the gateway journal rather than a shared reservation file.
Run `man 1 qfw-status` for application-state diagnostics and
`man 8 qfw-slurm-gateway` for administrator journal inspection.

Exit the allocated shell after `qfw-deactivate`. Slurm tears down the complete
heterogeneous allocation and attempts one allocation-wide QPM release.

</details>
