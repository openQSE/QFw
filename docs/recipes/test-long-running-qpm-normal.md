# Test a Site-owned NWQSim QPM from a Normal Allocation

This recipe validates the complete allocation-time path from Slurm through the
qfw-slurm gateway, directory service, and a long-running NWQSim QPM. It uses
one classical application node and one QPM reservation.

## 1. Confirm prerequisites

Complete [Configure a site-owned QPM](configure-long-running-qpm.md) and
[Configure the Docker Slurm environment](docker-slurm-environment.md). Enter
the controller as a regular user:

```bash
cd /path/to/QFw-SLURM-Cluster
./do_ssh.sh --user user-a
```

The login environment selects the official `/opt/openqse` QFw installation,
the site configuration, and a private run directory beneath the user's home.

## 2. Allocate the resources

Run `man salloc` for Slurm syntax. The installed qfw-slurm manuals describe
the allocator options and gateway protocol; use `man 7 qfw-slurm`
and `man 5 qfw-slurm-plugin.conf`.

```bash
salloc --partition=normal --nodes=1 --ntasks=1 --time=00:30:00 \
  --qpu=nwqsim \
  --workload-kind=hybrid \
  --circ-count=4 \
  --max-qubits=5 \
  --max-depth=100 \
  --max-shots=1024
```

The public QPU name maps to one exact QPM service ID. Evaluation does not hold
QPM capacity while Slurm waits for a node. After node assignment, pre-run
creates the final reservation and records it in the gateway journal.

## 3. Run through QFw

Run `man 1 qfw-activate`, `man 1 qfw-setup`, `man 1 qfw-srun`,
`man 1 qfw-teardown`, and `man 1 qfw-deactivate` for command details.

```bash
source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" \
  --venv "${QFW_VENV}"

cd "${QFW_SHARE_DIR}/examples"
qfw-setup
qfw-srun tests/test_qiskit_simple.py 5 nwqsim
qfw-teardown
qfw-deactivate
```

The example must emit a terminal JSON record with `"status": "ok"`. QFw
application teardown removes only application-owned runtime state. Slurm
allocation teardown releases the QPM reservation, while the site-owned QPM
and directory remain alive.

<details>
<summary>Reservation, result, and cleanup checks</summary>

```bash
srun /usr/bin/env | grep '^QFW_RESERVATIONS='
squeue -j "${SLURM_JOB_ID}"
qfw-status
```

`QFW_RESERVATIONS` is obtained from the gateway during remote SPANK
initialization. No reservation handoff file is shared between the controller
and compute node. Run `man 1 qfw-status` for application-state diagnostics and
`man 8 qfw-slurm-gateway` for administrator journal inspection.

Exit the allocated shell after `qfw-deactivate`. If a command fails, run
`qfw-teardown || true` and `qfw-deactivate || true` before exiting.

</details>
