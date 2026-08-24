# Configure the Docker Slurm Environment

Use this recipe to create the virtual Slurm cluster and a Python environment
that is visible to every cluster container. Commands marked **Host** run on the
workstation. Commands marked **Controller** run in the `slurmctld` container.

## 1. Clone and configure the cluster

**Host**

```bash
git clone git@github.com:openQSE/QFw-SLURM-Cluster.git
cd QFw-SLURM-Cluster

./do_configure.sh --prefix "$PWD/shared-dir"
git clone --recurse-submodules git@github.com:openQSE/QFw.git \
  "$PWD/shared-dir/QFw"
```

`do_configure.sh` records the shared host path in `qfw-install.env` and
`.env`. Docker Compose mounts that directory into every Slurm container as
`/workspace/qfw-container-base`.

## 2. Build and start the cluster

**Host**

```bash
./do_build.sh
./do_startup.sh
./do_ls.sh
./do_ssh.sh
```

`do_ssh.sh` opens a shell in the controller container.

## 3. Establish the shared QFw paths

**Controller**

```bash
export QFW_SHARED_ROOT=/workspace/qfw-container-base
export QFW_SRC="${QFW_SHARED_ROOT}/QFw"
export QFW_VENV="${QFW_SHARED_ROOT}/qfw-venv"
export QFW_BUILD="${QFW_SHARED_ROOT}/qfw-build"
```

This recipe does not select a runtime directory. The application and service
recipes set `QFW_RUN_BASE_DIR` when a run begins. Multinode recipes place it
below `QFW_SHARED_ROOT` so every container sees the same path.

## 4. Create the shared Python environment

**Controller**

```bash
python3 -m venv "${QFW_VENV}"
source "${QFW_VENV}/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "${QFW_SRC}/setup/build-requirements.txt"
python -m pip install -r "${QFW_SRC}/setup/requirements.txt"
```

Continue with either the
[non-standard installation](install-nonstandard-location.md) or the
[standard site installation](install-standard-location.md).

<details>
<summary>Environment verification and cluster cleanup</summary>

**Controller**

```bash
printf 'QFW_SHARED_ROOT=%s\n' "${QFW_SHARED_ROOT}"
printf 'VIRTUAL_ENV=%s\n' "${VIRTUAL_ENV}"
printf 'QFW_BUILD=%s\n' "${QFW_BUILD}"
python -c 'import sys; print(sys.executable); print(*sys.path, sep="\n")'
squeue -u "${USER}" || true
```

Confirm the shared mount from another node when diagnosing visibility:

```bash
srun --nodes=1 --ntasks=1 test -d "${QFW_SHARED_ROOT}"
```

Leave the Python environment with `deactivate`. On the host, stop the virtual
cluster without deleting its named volumes:

```bash
./do_stop.sh
```

</details>
