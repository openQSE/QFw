# Run a QFw Example in a Heterogeneous Slurm Allocation

This quick-start recipe places the application in heterogeneous group 0. Its
application-owned directory service, DVM, and NWQSim QPM run in group 1. Both
groups use the same installed QFw environment and shared runtime directory.

Run `man 1 qfw-activate`, `man 1 qfw_run_all.sh`, and
`man 1 qfw-deactivate` inside the cluster for command details.

## 1. Enter the cluster

Run these commands on the Docker host. Replace the first path with the local
QFw-SLURM-Cluster checkout.

```bash
cd /path/to/QFw-SLURM-Cluster
./do_ssh.sh
```

## 2. Request both groups and run the examples

Run these commands in the `slurmctld` container. Run `man salloc` for Slurm
heterogeneous-allocation option details.

```bash
salloc --nodes=1 --ntasks=1 --time=01:00:00 \
  : --nodes=1 --ntasks=1 --time=01:00:00

export QFW_SHARED_ROOT=/workspace/qfw-container-base
export QFW_RUN_BASE_DIR="${QFW_SHARED_ROOT}/qfw-runs"
mkdir -p "${QFW_RUN_BASE_DIR}"

source /opt/openqse/qfw/bin/qfw-activate \
  --venv /opt/openqse/qfw-venv

cd "${QFW_SHARE_DIR}/examples"
./qfw_run_all.sh --service-mode local --backend nwqsim

qfw-deactivate
```

`QFW_RUN_BASE_DIR` must be visible at the same pathname in both groups because
the service group writes its PRTE DVM URI there. See the
[detailed heterogeneous recipe](test-heterogeneous-allocation.md) for
placement checks, diagnostics, and the separate MPI smoke test.
