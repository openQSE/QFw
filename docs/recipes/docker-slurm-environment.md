# Configure the Docker Slurm Environment

Use this recipe to build the release virtual cluster with its official QFw,
DEFw, simulator, MUNGE, and qfw-slurm installations. Commands marked **Host**
run on the workstation. Commands marked **Controller** run in `slurmctld`.

## 1. Clone and configure the cluster

**Host**

```bash
git clone --branch release/v0.1 \
  git@github.com:openQSE/QFw-SLURM-Cluster.git
cd QFw-SLURM-Cluster
./do_configure.sh --prefix "$PWD/shared-dir"
```

The configured build obtains QFw and qfw-slurm from their upstream
`release/v0.1` branches. It installs QFw under `/opt/openqse/qfw`, its Python
environment under `/opt/openqse/qfw-venv`, and qfw-slurm under
`/opt/openqse/qfw-slurm`.

## 2. Build and start the cluster

**Host**

```bash
./do_build.sh
./do_startup.sh
./do_ls.sh
```

`do_startup.sh` also provisions `user-a`, `user-b`, and `user-c`, their
private homes, the client-readable `site.yaml`, and protected device-access
templates. It does not populate a real provider API key.

## 3. Verify the installed scheduling integration

Enter as root:

```bash
./do_ssh.sh
```

**Controller**

```bash
scontrol show config | grep -E 'JobSubmitPlugins|BurstBufferType'
man 7 qfw-slurm
man 1 qfw-slurm-driver
man 8 qfw-slurm-gateway
```

The configuration must report `JobSubmitPlugins=lua` and
`BurstBufferType=burst_buffer/lua`. The Slurm plugstack loads
`spank_quantum.so`. All three pieces are built against the same installed
Slurm version. Run `man 7 qfw-slurm` for their responsibilities and lifecycle.

MUNGE authenticates gateway traffic. The image contains one cluster key, and
every Slurm container starts `munged` before its Slurm daemon. Validate the
same credential on a compute node:

```bash
munge -n | ssh c1 unmunge >/dev/null
```

## 4. Enter as an application user

Leave the root shell, then run on the host:

```bash
./do_ssh.sh --user user-a
```

The login profile sets:

```text
QFW_INSTALL_PREFIX=/opt/openqse/qfw
QFW_VENV=/opt/openqse/qfw-venv
QFW_SHARED_ROOT=/workspace/qfw-container-base
QFW_RUN_BASE_DIR=/workspace/home/user-a/qfw-runs
QFW_SITE_CONFIG=/etc/openqse/qfw/site.yaml
```

`QFW_SHARED_ROOT` is used by QFw's directory connection record and by any
DVM-backed service whose runtime artifacts must cross nodes. qfw-slurm does
not use it to transfer accepted reservation tuples. The gateway journal and
burst-buffer retry state remain protected, controller-local files; remote
SPANK retrieves tuples from the gateway.

Continue with the [long-running QPM configuration](configure-long-running-qpm.md)
as root, then use the [normal](test-long-running-qpm-normal.md) or
[heterogeneous](test-long-running-qpm-heterogeneous.md) application recipe as
a regular user.

<details>
<summary>Installation verification and cluster cleanup</summary>

**Controller**

```bash
test -x /opt/openqse/qfw/bin/qfw-activate
test -x /opt/openqse/qfw-slurm/bin/qfw-slurm-driver
test -x /usr/lib64/slurm/spank_quantum.so
test -x /usr/lib64/slurm/job_submit_lua.so
test -x /usr/lib64/slurm/burst_buffer_lua.so
pgrep -a munged
sinfo -Nel
```

Run `man 1 qfw_slurm_install.sh` for standalone qfw-slurm installation and
`man 5 qfw-slurm-gateway.yaml` for gateway configuration.

On the host, stop the cluster without deleting its named volumes:

```bash
./do_stop.sh
```

</details>
