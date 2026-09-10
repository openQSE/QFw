# Run a QFw Example on One Slurm Node

This quick-start recipe enters the QFw virtual Slurm cluster and runs the
installed example suite with an application-owned NWQSim QPM. The allocation,
directory service, DVM, and QPM are owned by this application workflow.

Run `man 1 qfw-activate`, `man 1 qfw_run_all.sh`, and
`man 1 qfw-deactivate` inside the cluster for command details.

## 1. Enter the cluster

Run these commands on the Docker host. Replace the first path with the local
QFw-SLURM-Cluster checkout.

```bash
cd /path/to/QFw-SLURM-Cluster
./do_ssh.sh
```

## 2. Request one node and run the examples

Run these commands in the `slurmctld` container. `salloc` opens the interactive
allocation used by the commands that follow. Run `man salloc` for Slurm option
details.

```bash
salloc --nodes=1 --ntasks=1 --time=01:00:00

export QFW_SHARED_ROOT=/workspace/qfw-container-base
export QFW_RUN_BASE_DIR="${QFW_SHARED_ROOT}/qfw-runs"
mkdir -p "${QFW_RUN_BASE_DIR}"

source /opt/openqse/qfw/bin/qfw-activate \
  --venv /opt/openqse/qfw-venv

cd "${QFW_SHARE_DIR}/examples"
./qfw_run_all.sh --service-mode local --backend nwqsim

qfw-deactivate
```

The runner records its summary beneath
`${QFW_RUN_BASE_DIR}/examples-run-<timestamp>`. See the
[detailed one-node recipe](test-same-node.md) for diagnostics and the separate
MPI smoke test.
