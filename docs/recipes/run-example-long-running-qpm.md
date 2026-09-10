# Run a QFw Example Against a Long-running QPM

This quick start requests NWQSim capacity when Slurm creates the allocation,
then runs one installed QFw example against the accepted site-owned QPM. The
application does not start or stop the directory service, QPM, or gateway.

The administrator must first complete [Configure a site-owned
QPM](configure-long-running-qpm.md). The cluster must also have the qfw-slurm
job-submit, burst-buffer, and SPANK integration described in [Configure the
Docker Slurm environment](docker-slurm-environment.md).

Run `man salloc`, `man 1 qfw-activate`, `man 1 qfw-setup`,
`man 1 qfw-srun`, `man 1 qfw-teardown`, and `man 1 qfw-deactivate` for the
commands used below.

## 1. Enter the cluster as an application user

Run on the Docker host:

```bash
cd /path/to/QFw-SLURM-Cluster
./do_ssh.sh --user user-a
```

The cluster login profile supplies `QFW_INSTALL_PREFIX`, `QFW_VENV`,
`QFW_SHARED_ROOT`, `QFW_RUN_BASE_DIR`, and `QFW_SITE_CONFIG`.

## 2. Request classical and quantum resources

The qfw-slurm options belong on `salloc`, not `srun`. These bounds describe
the largest workload the allocation may submit:

```bash
salloc --partition=normal --nodes=1 --ntasks=1 --time=00:30:00 \
  --qpu=nwqsim \
  --workload-kind=hybrid \
  --circ-count=4 \
  --max-qubits=5 \
  --max-depth=100 \
  --max-shots=1024
```

Slurm performs non-binding admission evaluation while the job is pending. It
creates the QPM reservation only after classical nodes are assigned.

## 3. Run the example

Run inside the allocated shell:

```bash
source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" \
  --venv "${QFW_VENV}"

cd "${QFW_SHARE_DIR}/examples"
qfw-setup
qfw-srun tests/test_qiskit_simple.py 5 nwqsim
qfw-teardown
qfw-deactivate
```

Remote SPANK asks the gateway for the allocation's accepted reservation and
injects a compact value such as `[["nwqsim","17"]]` into
`QFW_RESERVATIONS`. It does not read a controller-generated reservation file.
QFw resolves the service through the directory service and uses that tuple for
QPM calls.

Use the explicit `qfw-setup` and `qfw-srun` sequence for allocation-plugin
validation. `qfw_run_all.sh` remains useful for its own managed test workflow,
but it is not the authoritative check that one Slurm-created reservation is
reused.

<details>
<summary>Verification and cleanup</summary>

Inside the allocation, verify the injected context with an ordinary Slurm
step:

```bash
srun /usr/bin/env | grep '^QFW_RESERVATIONS='
qfw-status
```

Run `man 1 qfw-status` for the runtime-state output. The terminal example
record must report `"status": "ok"`. Leaving the allocated shell releases the
Slurm allocation; burst-buffer teardown then attempts to release every QPM
reservation.

If the application exits early, run:

```bash
qfw-teardown || true
qfw-deactivate || true
exit
```

See [Recover interrupted services and allocations](recover-services.md) when
the gateway, directory service, or QPM is unavailable.

</details>
