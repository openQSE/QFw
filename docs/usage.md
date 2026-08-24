# QFw Usage

This guide covers the shortest supported path to clone, build, configure, and
run QFw. See the [project README](../README.md) for architecture, development,
and complete example details. See the [QFw recipes](recipes/README.md) for
focused installation, service-lifecycle, and Slurm-placement procedures.

## Contents

- [Docker quick start](#docker-quick-start)
- [Prerequisites](#prerequisites)
- [Clone](#clone)
- [Create a Python environment](#python-environment)
- [Build and install](#build-and-install)
- [Activate QFw](#activate)
- [Configuration](#configuration)
- [Choose a deployment](#deployment)
- [Run examples](#examples)
- [Verify a run](#verification)
- [Failure recovery](#failure-recovery)

## Docker Quick Start

This recipe creates the QFw Slurm development cluster, builds a shared QFw and
DEFw installation, and runs one NWQ-Sim example. Commands marked **Host** run
on the workstation. Commands marked **Controller** run inside the
`slurmctld` container.

The recipe uses the cluster repository's default `shared-dir`. That directory
is mounted in every container as `/workspace/qfw-container-base`.

### 1. Clone And Configure The Cluster

**Host**

```bash
git clone git@github.com:openQSE/QFw-SLURM-Cluster.git
cd QFw-SLURM-Cluster

./do_configure.sh --prefix "$PWD/shared-dir"
git clone --recurse-submodules git@github.com:openQSE/QFw.git \
  "$PWD/shared-dir/QFw"
```

`do_configure.sh` records the image settings and shared-directory path in
`qfw-install.env` and `.env`. The remaining cluster scripts read those files.

### 2. Build And Start The Cluster

**Host**

```bash
./do_build.sh
./do_startup.sh
./do_ls.sh
./do_ssh.sh
```

`do_startup.sh` starts the containers, waits for SlurmDBD, and registers the
cluster. `do_ssh.sh` opens a shell in `slurmctld`.

### 3. Build And Install QFw And DEFw

**Controller**

```bash
export QFW_BASE=/workspace/qfw-container-base
export QFW_SRC="$QFW_BASE/QFw"
export QFW_VENV="$QFW_BASE/qfw-venv"
export QFW_BUILD="$QFW_BASE/qfw-build"
export QFW_PREFIX="$QFW_BASE/qfw-install"

python3 -m venv "$QFW_VENV"
source "$QFW_VENV/bin/activate"
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r "$QFW_SRC/setup/build-requirements.txt"
python -m pip install -r "$QFW_SRC/setup/requirements.txt"

cmake -S "$QFW_SRC" -B "$QFW_BUILD" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_INSTALL_PREFIX="$QFW_PREFIX" \
  -DQFW_BUILD_BUNDLED_DEFW=ON
cmake --build "$QFW_BUILD" -j "$(nproc)"
cmake --install "$QFW_BUILD"
```

The build tree contains intermediate output. The install tree contains the
runnable QFw and DEFw installation. Both remain visible to every Slurm
container through the shared mount.

### 4. Activate And Check Configuration

**Controller**

```bash
source "$QFW_PREFIX/bin/qfw-activate" --venv "$QFW_VENV"

printf 'QFW_PREFIX=%s\n' "$QFW_PREFIX"
printf 'QFW_SITE_CONFIG=%s\n' "$QFW_SITE_CONFIG"
printf 'QFW_SHARE_DIR=%s\n' "$QFW_SHARE_DIR"
```

No configuration file needs to be created or edited for this local NWQ-Sim
recipe. Installation provides these defaults:

```text
$QFW_SHARE_DIR/config/site.yaml
$QFW_SHARE_DIR/config/services/local-services.yaml
$QFW_SHARE_DIR/config/services/site-services.yaml
```

The example generates a local runtime file that selects only the NWQ-Sim
service. Its directory service, QPM, and simulator are owned by the Slurm job.
Hardware runs and site-owned services require the site configuration described
under [Configuration](#configuration).

### 5. Allocate A Node And Run An Example

**Controller**

```bash
salloc --nodes=1 --ntasks=1 --time=00:10:00

cd "$QFW_SHARE_DIR/examples"
./qfw_qiskit_simple.sh 4
```

The example wrapper executes the supported lifecycle:

```text
qfw-setup -> reservation driver -> qfw-srun -> qfw-teardown
```

Success requires a completed circuit result, a released reservation, and a
zero exit status. Submission by itself is not a successful run. Exit the
allocation shell and then the controller shell after inspecting the output.

### 6. Stop The Cluster

**Host**

```bash
./do_stop.sh
```

This preserves the cluster's named volumes. Use `./do_stop.sh delete` when the
containers and named volumes are no longer needed.

<details id="prerequisites">
<summary><strong>1. Prerequisites</strong></summary>

QFw requires the following software:

- Linux with a C and C++ compiler, CMake, Git, and Python 3.
- A Python virtual environment visible to every node that runs the application
  or a QFw service.
- Slurm and a compatible MPI/PRTE installation for distributed execution.
- Provider or simulator dependencies for the selected backend.

QFw installs its service APIs and bundled QHW packages. Simulator executables
such as NWQ-Sim and optional hardware client libraries must be provided by the
host, container image, module environment, or Python virtual environment.

</details>

<details id="clone">
<summary><strong>2. Clone</strong></summary>

Clone QFw and all submodules:

```bash
git clone --recurse-submodules git@github.com:openQSE/QFw.git
cd QFw
```

Initialize submodules in an existing checkout with:

```bash
git submodule update --init --recursive
```

</details>

<details id="python-environment">
<summary><strong>3. Create A Python Environment</strong></summary>

Create the environment on storage visible to every node that will use it:

```bash
python3 -m venv /path/to/qfw-venv
source /path/to/qfw-venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r setup/build-requirements.txt
python -m pip install -r setup/requirements.txt
```

Install any simulator, application, or hardware-provider Python dependencies
into the same environment.

</details>

<details id="build-and-install">
<summary><strong>4. Build And Install</strong></summary>

Choose separate source, build, and installation directories:

```bash
export QFW_SRC="$PWD"
export QFW_BUILD="$PWD/build"
export QFW_PREFIX="$HOME/.local/qfw"

cmake -S "$QFW_SRC" -B "$QFW_BUILD" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo \
  -DCMAKE_INSTALL_PREFIX="$QFW_PREFIX" \
  -DQFW_BUILD_BUNDLED_DEFW=ON
cmake --build "$QFW_BUILD" -j
cmake --install "$QFW_BUILD"
```

This guide uses `$HOME/.local/qfw` as the example installation prefix. QFw
does not choose that path independently. The value passed through
`CMAKE_INSTALL_PREFIX` or `qfw_install.sh --prefix` is the installation root.

`QFW_BUILD` contains intermediate build output. `QFW_PREFIX` contains the
runnable installation. The bundled DEFw build is installed into the same
prefix.

The convenience installer performs the same operation:

```bash
./setup/qfw_install.sh --prefix "$QFW_PREFIX" --with-defw
```

The installation must be visible at the same path on every node that runs QFw.

</details>

<details id="activate">
<summary><strong>5. Activate QFw</strong></summary>

Activate QFw and the shared Python environment together:

```bash
source "$QFW_PREFIX/bin/qfw-activate" --venv /path/to/qfw-venv
```

QFw also works without an explicit virtual environment:

```bash
source "$QFW_PREFIX/bin/qfw-activate"
```

If a virtual environment is already active, QFw preserves it. An explicit
`--venv` argument takes precedence over a different active environment.

Activation prepares paths and commands and prepends `(qfw) ` to the existing
prompt. The prefix is supplied by QFw rather than the virtual environment, and
existing prompt behavior such as working-directory expansion remains intact.
Run `qfw-deactivate` to restore the previous QFw environment and prompt. The
selected virtual environment remains active until its own `deactivate` command
is run. Activation does not start QFw services.

### Installed Paths And Environment Variables

The build commands in this guide use `QFW_BASE`, `QFW_SRC`, `QFW_VENV`, and
`QFW_BUILD` as shell conveniences. QFw does not interpret those names at
runtime. `QFW_VENV`, for example, becomes meaningful only when its value is
passed to `qfw-activate --venv`.

QFw environment variables fall into three groups. Operators select the first
group, activation derives the second group, and `qfw-setup` publishes the third
group for commands and services in the active run. In these tables, `<prefix>`
is the installation prefix selected during the build. For the commands above,
`<prefix>` is `$HOME/.local/qfw`.

#### Operator Configuration

Set these variables before activation or `qfw-setup` when the defaults are not
appropriate. An explicit command-line option takes precedence over its
environment-variable counterpart.

| Variable | Default | Meaning and use |
| --- | --- | --- |
| `QFW_PREFIX` | Prefix containing `qfw-activate` | QFw installation root. Activation normally derives this value from its own installed path. |
| `QFW_CONFIG_DIR` | `/etc/openqse/qfw` | Conventional site-configuration root. This value does not select a site file by itself. |
| `QFW_SITE_CONFIG` | `<prefix>/share/qfw/config/site.yaml` | Site configuration selected by `qfw-setup`; overridden by `qfw-setup --site-config`. |
| `QFW_RUN_BASE_DIR` | `${TMPDIR:-/tmp}/qfw-runs` | Parent directory for per-run state, logs, PID/readiness files, the `current` marker, and the PRTE DVM URI. It must be writable. For a heterogeneous or multi-node allocation, it must be visible at the same path on every participating node; do not use node-local `/tmp` in that case. |
| `QFW_RUNTIME_PROFILE` | Unset | Name of a packaged runtime profile such as `local` or `hybrid`; overridden by `qfw-setup --profile`. |
| `QFW_RUNTIME_CONFIG` | Unset | Explicit runtime YAML path; overridden by `qfw-setup --runtime-config` and preferred over `QFW_RUNTIME_PROFILE`. |
| `QFW_QPM_RESOLVER_SCOPE_ORDER` | Runtime configuration | Advanced comma-separated override for QPM resolution scopes, such as `allocation-local,site`. Normal runs should select a runtime profile instead. |
| `QFW_SITE_DIRSVC_ENDPOINTS` | Site configuration | Advanced comma-separated override for site directory-service endpoints. Normal runs should configure them in `site.yaml`. |
| `QFW_SITE_DIRSVC_NAME` | `qfw-site-dirsvc` | Directory-service name used with an endpoint override. |
| `QFW_DIRSVC_CONNECT_TIMEOUT_SECONDS` | `300` | Directory readiness timeout used with an endpoint override, in seconds. |
| `QFW_RUN_ID` | Generated UUID | Optional caller-supplied run identifier. Use a unique value for each concurrently active runtime. |

`QFW_TMP_PATH` is accepted as a compatibility fallback for
`QFW_RUN_BASE_DIR`. New scripts and site configurations should use
`QFW_RUN_BASE_DIR`.

For a heterogeneous allocation, select a shared run base before activation.
The Docker Slurm cluster mounts `/workspace/qfw-container-base` on every node,
so a suitable setup is:

```bash
export QFW_RUN_BASE_DIR="/workspace/qfw-container-base/qfw-runs/job-${SLURM_JOB_ID}"
mkdir -p "$QFW_RUN_BASE_DIR"
source "$QFW_PREFIX/bin/qfw-activate" --venv "$QFW_VENV"
```

#### Activation-Derived Paths

`qfw-activate` exports these paths. Callers normally inspect rather than set
them.

| Variable | Default after activation | Meaning and use |
| --- | --- | --- |
| `QFW_BIN_PATH` | `<prefix>/bin` | Public QFw commands added to `PATH`. |
| `QFW_LIBEXEC_DIR` | `<prefix>/libexec/qfw` | Private command helpers used by the public commands. |
| `QFW_SHARE_DIR` | `<prefix>/share/qfw` | Installed examples and packaged site, runtime, service, and device configuration. |
| `DEFW_PREFIX` | `<prefix>` | Bundled DEFw installation root. |
| `DEFW_CONFIG_PATH` | `<prefix>/share/defw/config/defw_generic.yaml` | DEFw runtime configuration. |

#### Prepared Run Environment

`qfw-setup` records these values in `qfw-runtime-env.sh` and
`state/runtime-state.json` below the run directory. They are implementation and
launcher context; applications and operators generally should not set them
directly.

| Variable | Published meaning |
| --- | --- |
| `QFW_RUN_ID` | Identifier for the prepared run. |
| `QFW_RUN_TMP_PATH` | Full path to the current run directory below `QFW_RUN_BASE_DIR`. |
| `QFW_LOG_DIR` | Service log directory for the current run. |
| `QFW_DVM_URI_PATH` | PRTE DVM URI file used to coordinate process launch. This is why a heterogeneous run requires a shared `QFW_RUN_BASE_DIR`. |
| `QFW_LOCAL_DIRSVC_ENDPOINT`, `QFW_LOCAL_DIRSVC_NAME` | Allocation-owned directory-service identity selected during setup. |
| `QFW_LOCAL_SERVICE_CONFIG`, `QFW_SERVICE_SCOPE` | Service manifest and ownership scope selected by the runtime profile. |
| `QFW_ALLOCATION_MODE` | Detected launch mode: local, normal Slurm, or heterogeneous Slurm. |
| `QFW_GROUP_0_NODELIST`, `QFW_GROUP_1_NODELIST`, `QFW_GROUPS` | Normalized application and quantum-service placement derived from the Slurm allocation. |
| `QFW_RESERVATION_ID` | Reservation context supplied to an application by a trusted Slurm/site launcher or by the example driver. It is not a user credential. |

Example-only controls such as `QFW_RUN_ALL_BACKEND` are documented with their
wrappers in [examples/README.md](../examples/README.md). Provider credentials
are service configuration and must not be placed in these application runtime
variables.

Activation preserves explicit `QFW_SITE_CONFIG` and `QFW_RUN_BASE_DIR` values.
Confirm the active paths with:

```bash
printf 'QFW_PREFIX=%s\n' "$QFW_PREFIX"
printf 'QFW_SHARE_DIR=%s\n' "$QFW_SHARE_DIR"
printf 'QFW_SITE_CONFIG=%s\n' "$QFW_SITE_CONFIG"
printf 'QFW_RUN_BASE_DIR=%s\n' "$QFW_RUN_BASE_DIR"
printf 'DEFW_PREFIX=%s\n' "$DEFW_PREFIX"
```

</details>

<details id="configuration" closed>
<summary><strong>6. Configuration</strong></summary>

QFw configuration is divided by ownership. Site administrators configure
shared infrastructure and protected devices. Users select the runtime for a
job. Applications receive execution context and select the workload they run.

| Owner | Configuration | Purpose |
| --- | --- | --- |
| Site administrator | Site, service, and device files | Define shared infrastructure and protected provider access |
| User or launcher | Runtime profile and Python environment | Select discovery order and job-owned services |
| Application | Backend requirements and workload inputs | Select a registered service and submit reserved work |

Across the supported local, hybrid, and site deployments, QFw uses five
configuration file types. The runtime profile is separate from the four site
and service files. A particular deployment does not necessarily read all five;
for example, a site-only job does not use the local service manifest.

<details>
<summary><strong><code>$QFW_SHARE_DIR/config/site.yaml</code></strong></summary>

```yaml
install:
  qfw-prefix: ${QFW_PREFIX}
  defw-prefix: ${DEFW_PREFIX}

directory-service:
  name: qfw-site-dirsvc
  listen-port: 8090
  connect-timeout-seconds: 300
  connection-file: ${QFW_SHARED_ROOT}/qfw-site-services/directory-service.json

service:
  manifest: ${QFW_PREFIX}/share/qfw/config/services/site-services.yaml
  device-access-config: /etc/openqse/qfw/device/device-access.yaml

qpm:
  completion-queues:
    retention:
      completion-ttl-seconds: 3600
      terminal-reservation-retention-seconds: 3600
      max-records-per-reservation: 1024
      max-bytes-per-reservation: 67108864
      purge-interval-seconds: 60
```

QFw expands braced environment references while reading configuration paths.
`qfw-activate` sets `QFW_PREFIX` and `DEFW_PREFIX` before these files are read.
The site administrator sets `QFW_SHARED_ROOT` to a path shared by service and
application nodes. An unset or empty referenced variable is a configuration
error.

| Owner | Used by and when | Purpose |
| --- | --- | --- |
| Site administrator | `qfw-setup`, `qfw-dir-svc`, and `qfw-qpm-svc` resolve it. The QPM manager passes the selected site path to its service process. | Select the installation, site directory, service-side files, and common QPM settings. |

</details>

<details>
<summary><strong><code>Runtime configuration files</code></strong></summary>

`$QFW_SHARE_DIR/config/runtime.yaml`

```yaml
resolver:
  scope-order:
    - site
```

`$QFW_SHARE_DIR/config/runtime/local.yaml`

```yaml
resolver:
  scope-order:
    - local

local-services:
  start-prte: true
  start-dirsvc: true
  start-qpm: true
  dirsvc:
    name: qfw-local-dirsvc
    bind-host: 127.0.0.1
    port: auto
  service-manifest: ${QFW_PREFIX}/share/qfw/config/services/local-services.yaml
```

`$QFW_SHARE_DIR/config/runtime/hybrid.yaml`

```yaml
resolver:
  scope-order:
    - local
    - site

local-services:
  start-prte: true
  start-dirsvc: true
  start-qpm: true
  dirsvc:
    name: qfw-local-dirsvc
    bind-host: 127.0.0.1
    port: auto
  service-manifest: ${QFW_PREFIX}/share/qfw/config/services/local-services.yaml
```

| Owner | Used by and when | Purpose |
| --- | --- | --- |
| User or launcher | `qfw-setup` reads one runtime file for each job. | Select directory discovery order and whether the allocation starts PRTE, a directory service, and QPM services. |

When `start-prte` is absent, QFw uses the value of `start-qpm` as its default.
This allows a small local runtime file to request QPM startup without repeating
the PRTE setting.

</details>

<details>
<summary><strong><code>$QFW_SHARE_DIR/config/services/local-services.yaml</code></strong></summary>

```yaml
mpi-launch:
  launcher: mpirun
  allow-run-as-root: auto
  export-env:
    - LD_LIBRARY_PATH
  bind-to: core
  map-by: ppr:1:l3cache
  mca:
    btl: ^tcp,ofi,vader,openib
    pml: ^ucx
    mtl: ofi
    opal_common_ofi_provider_include: shm+cxi:linkx

services:
  - name: nwqsim
    module: svc_nwqsim_qpm
    load-modules: svc_nwqsim_qpm,api_launcher
    agent-prefix: qpm_nwqsim
    target: group1-head
    assigned-hosts: group1
    assigned-hosts-env: QFW_QPM_ASSIGNED_HOSTS
    provider-launch:
      type: mpi
      wrapper: null

  - name: tnqvm
    module: svc_tnqvm_qpm
    load-modules: svc_tnqvm_qpm,api_launcher
    agent-prefix: qpm_tnqvm
    target: group1-head
    assigned-hosts: group1
    assigned-hosts-env: QFW_QPM_ASSIGNED_HOSTS
    provider-launch:
      type: mpi
      wrapper: gpuwrapper.sh

  - name: fake-iqm
    module: svc_fake_iqm_qpm
    load-modules: svc_fake_iqm_qpm,api_launcher
    agent-prefix: qpm_fake_iqm
    target: group1-head
    assigned-hosts: group1
    assigned-hosts-env: QFW_QPM_ASSIGNED_HOSTS
    device-id: fake-iqm-20q
    provider-launch:
      type: internal

```

| Owner | Used by and when | Purpose |
| --- | --- | --- |
| QFw package | `qfw-setup` reads it when planning allocation-owned services. The `qfw-qpm-svc` lifecycle engine resolves the selected entry when launching a QPM. | Define local simulator services, allocation placement, and provider launch settings. |

The current launcher consumes the service name, module, loaded modules,
placement, assigned-host fields, ports, and device ID. Simulator launch code
also consumes `provider-launch.wrapper`. The packaged `agent-prefix` and
`provider-launch.type` fields are reserved metadata and do not currently alter
launch behavior.

</details>

<details>
<summary><strong><code>$QFW_SHARE_DIR/config/services/site-services.yaml</code></strong></summary>

```yaml
services:
  - name: iqm-ornl-20q
    module: svc_iqm_qpm
    load-modules: svc_iqm_qpm,api_launcher
    agent-prefix: qpm_iqm
    device-id: ornl-iqm-20q
    provider-launch:
      type: remote-api

  - name: shim-ornl-20q
    module: svc_lib_qpm
    load-modules: svc_lib_qpm,api_launcher
    agent-prefix: qpm_shim
    device-id: ornl-iqm-20q
    provider-launch:
      type: qrmi-qdmi
```

| Owner | Used by and when | Purpose |
| --- | --- | --- |
| Site administrator | `qfw-qpm-svc` reads the service selected by `--service-id`. Applications discover running instances through the site directory. | Define hardware-facing QPM implementations that a site operator can start. |

</details>

<details>
<summary><strong><code>/etc/openqse/qfw/device/device-access.yaml</code></strong></summary>

```yaml
qpus:
  ornl-iqm-20q:
    provider: iqm
    provider-device-id: default
    url: https://qccsw.ccs.ornl.gov/
    credential-db: qpu_users.json
```

| Owner | Used by and when | Purpose |
| --- | --- | --- |
| Site administrator | A hardware QPM reads it during service initialization and reservation credential lookup. Applications do not read it. | Define provider devices, endpoints, and credential-provider references. |

</details>

### Site Configuration

The site administrator owns the shared QFw installation, site directory, and
long-running QPM services. A site-only service plane directly uses three
site-owned files: `site.yaml`, the site service manifest, and the device-access
file. Jobs additionally select a runtime file. The local service manifest is
used only when a local or hybrid runtime starts allocation-owned services.

#### Site File

The client-readable site file connects the other configuration pieces. A
production site normally installs it as `/etc/openqse/qfw/site.yaml`:

```yaml
install:
  qfw-prefix: /opt/openqse/qfw/current
  defw-prefix: /opt/openqse/defw/current

directory-service:
  name: ornl-site-dirsvc
  listen-port: 8090
  connect-timeout-seconds: 300
  connection-file: /shared/openqse/qfw/directory-service.json

service:
  manifest: /etc/openqse/qfw/services/site-services.yaml
  device-access-config: /etc/openqse/qfw/device/device-access.yaml

qpm:
  completion-queues:
    retention:
      completion-ttl-seconds: 3600
      terminal-reservation-retention-seconds: 3600
      max-records-per-reservation: 1024
      max-bytes-per-reservation: 67108864
      purge-interval-seconds: 60
```

The file contains paths to protected configuration, but no API keys or provider
credentials. The packaged local fallback is:

```text
$QFW_SHARE_DIR/config/site.yaml
```

For a local installation, this fallback is selected automatically by
`qfw-activate`. The user does not need to set `QFW_SITE_CONFIG`.

#### Service Manifests

QFw separates allocation-owned services from site-owned hardware QPMs. The
packaged local manifest contains simulators and fake providers that
`qfw-setup` may start inside a job:

```text
$QFW_SHARE_DIR/config/services/local-services.yaml
```

The site manifest contains hardware-facing implementations that an operator
may start as long-running services:

```text
$QFW_SHARE_DIR/config/services/site-services.yaml
```

A representative IQM entry is:

```yaml
services:
  - name: iqm-ornl-20q
    module: svc_iqm_qpm
    load-modules: svc_iqm_qpm,api_launcher
    agent-prefix: qpm_iqm
    device-id: ornl-iqm-20q
    provider-launch:
      type: remote-api
```

A production site normally maintains its active manifest at
`/etc/openqse/qfw/services/site-services.yaml`. The `service.manifest` field in
`site.yaml` selects it. The site operator starts a service by ID; application
jobs never pass a service-manifest path. The `device-id` must match the active
device-access configuration.

#### Common QPM Configuration

The `qpm` section of `site.yaml` defines runtime behavior shared by simulator
and hardware QPMs. Each QPM reads this section from the path in
`QFW_SITE_CONFIG` during initialization:

```yaml
qpm:
  completion-queues:
    retention:
      completion-ttl-seconds: 3600
      terminal-reservation-retention-seconds: 3600
      max-records-per-reservation: 1024
      max-bytes-per-reservation: 67108864
      purge-interval-seconds: 60
```

Completion queues hold finished task results until an application reads them.
Retention limits prevent a long-running QPM from accumulating unread results
without bounds.

| Field | Default | Meaning |
| --- | --- | --- |
| `completion-ttl-seconds` | `3600` | Maximum age of an unread completed result |
| `terminal-reservation-retention-seconds` | `3600` | Time results remain readable after release, cancellation, or expiration |
| `max-records-per-reservation` | `1024` | Maximum completed records retained for one reservation |
| `max-bytes-per-reservation` | `67108864` | Maximum measurable result data retained per reservation, in bytes |
| `purge-interval-seconds` | `60` | Interval between scans for expired completion records |

When a reservation exceeds a record or byte limit, the QPM evicts its oldest
completed records. The byte limit is 64 MiB by default. If a result's size
cannot be measured, the QPM still enforces the age and record-count limits.
Explicit non-positive values are invalid.

Simulator-specific MPI and wrapper settings live with the corresponding
entries in `local-services.yaml`; hardware QPMs do not receive those settings.

#### Device Access File

The device-access file maps the manifest's logical device ID to a provider,
provider device, endpoint, and credential provider. A simplified IQM entry is:

```yaml
qpus:
  ornl-iqm-20q:
    provider: iqm
    provider-device-id: default
    url: https://qccsw.ccs.ornl.gov/
    credential-db: qpu_users.json
```

`credential-db` is resolved relative to `device-access.yaml` when it is not an
absolute path. The credential database contains provider secrets and must be
readable only by the account running the QPM. Applications do not receive this
file or its contents.

The site-owned device file commonly lives at
`/etc/openqse/qfw/device/device-access.yaml`. The
`device-access-config` field in `site.yaml` selects it for
`qfw-qpm-svc` and its QPM process.

#### Select The Site File

`qfw-activate` does not search `/etc` automatically. A production module or
environment script selects the active site file before activation:

```bash
export QFW_SITE_CONFIG=/etc/openqse/qfw/site.yaml
source /opt/openqse/qfw/current/bin/qfw-activate
```

For example, `/etc/openqse/qfw/env.sh` may export `QFW_SITE_CONFIG`. A module or
wrapper can source both files:

```bash
source /etc/openqse/qfw/env.sh
source /opt/openqse/qfw/current/bin/qfw-activate
```

`qfw-setup` selects the site file in this order:

1. `qfw-setup --site-config <path>`
2. `QFW_SITE_CONFIG`
3. `$QFW_SHARE_DIR/config/site.yaml`

An operator can override the site file for one setup or role-manager command
with `--site-config /path/to/site.yaml`.

#### Start A Site Service

After `site.yaml`, its site manifest, and its device-access file are defined,
an administrator starts the directory service and IQM QPM independently:

```bash
export QFW_SITE_CONFIG=/etc/openqse/qfw/site.yaml
source /opt/openqse/qfw/current/bin/qfw-activate
qfw-dir-svc start \
  --run-dir /shared/openqse/qfw/services/directory \
  --site-config "$QFW_SITE_CONFIG" \
  --scope site \
  --node dirsvc01
qfw-qpm-svc start \
  --run-dir /shared/openqse/qfw/services/iqm-ornl-20q \
  --service-id iqm-ornl-20q \
  --site-config "$QFW_SITE_CONFIG" \
  --scope site \
  --node qpm01
```

`qfw-dir-svc` publishes the resolved endpoint to the connection file selected
by `directory-service.connection-file`. `qfw-qpm-svc` reads that connection
record automatically through `site.yaml`, resolves the site service manifest
and device-access path, and starts only the requested service. The launched
QPM receives `QFW_SITE_CONFIG` and reads common QPM settings from that file.

The service launcher passes the protected device-access path only to the QPM
process. The QPM reads that file and its referenced credential database.
Applications use the client-readable directory information from `site.yaml`;
they do not receive the device-access path or its credential contents.

### User Configuration

The user selects a Python environment and runtime profile. The runtime controls
service discovery and whether the job starts allocation-owned services.

QFw installs three runtime files:

| Selection | Installed file | Discovery order | Services started by the job |
| --- | --- | --- | --- |
| No profile | `$QFW_SHARE_DIR/config/runtime.yaml` | Site only | None |
| `--profile local` | `$QFW_SHARE_DIR/config/runtime/local.yaml` | Local only | Local directory and QPM services |
| `--profile hybrid` | `$QFW_SHARE_DIR/config/runtime/hybrid.yaml` | Local, then site | Local directory and QPM services |

The default runtime file contains:

```yaml
resolver:
  scope-order:
    - site
```

The packaged local profile contains:

```yaml
resolver:
  scope-order:
    - local

local-services:
  start-prte: true
  start-dirsvc: true
  start-qpm: true
  dirsvc:
    name: qfw-local-dirsvc
    bind-host: 127.0.0.1
    port: auto
  service-manifest: ${QFW_PREFIX}/share/qfw/config/services/local-services.yaml
```

The packaged hybrid profile has the same `local-services` block. Its resolver
checks the allocation-owned directory before the site directory:

```yaml
resolver:
  scope-order:
    - local
    - site
```

Neither packaged local profile contains a `services` list. A direct
`qfw-setup --profile local` invocation therefore starts every service in the
selected manifest. Backend-aware example wrappers add
`--service-id <backend-service>` so they start only the requested QPM without
generating another runtime file. `--service-id` requires a runtime with a
`local-services` section.

Runtime selection uses this order:

1. `qfw-setup --runtime-config <path>`
2. `qfw-setup --profile <name>`
3. `QFW_RUNTIME_CONFIG`
4. `QFW_RUNTIME_PROFILE`
5. `$QFW_SHARE_DIR/config/runtime.yaml`

Command-line selections take precedence over environment variables.

The runtime parser also accepts the `direct` resolver scope for low-level
integration paths. `QFW_QPM_RESOLVER_SCOPE_ORDER` can override the configured
scope order, and `QFW_SITE_DIRSVC_ENDPOINTS` can override the site directory
endpoint list. Normal local and production runs should use the packaged
profiles and site configuration instead of these integration overrides.

Configuration validation is section-specific rather than schema-wide. Invalid
values used by QFw are rejected, but unknown keys are generally ignored. Use
the canonical key spelling shown in the installed templates.

For a normal local run, the user selects the packaged local profile:

```bash
source "$QFW_PREFIX/bin/qfw-activate" --venv /path/to/qfw-venv
qfw-setup --profile local
```

`qfw-setup` creates the application run directory and delegates each requested
component to the split lifecycle managers. It starts one application-owned
directory through `qfw-dir-svc`, then starts each selected QPM through
`qfw-qpm-svc`. Each QPM manager also owns its optional PRTE DVM. The generated
directory connection record is added to application runtime state
automatically; users do not source a service environment file or invoke the
role commands themselves. `qfw-teardown` stops the QPM managers and directory
manager in reverse order before removing application state.

For a production cluster, the site module or environment script selects the
site file. The user then runs `qfw-setup` without a profile to use site services
only:

```bash
source /etc/openqse/qfw/env.sh
source /opt/openqse/qfw/current/bin/qfw-activate \
  --venv "$HOME/venvs/my-qfw-app"
qfw-setup
```

Normal users do not configure provider API keys, directory internals, or
long-running service launch policy. Packaged profiles also provide listener
port defaults. Integrators who create custom runtime files or service manifests
can set local service port bases and per-service listener ports.

### Application Configuration

QFw applications run through one lifecycle:

```text
qfw-setup -> qfw-status -> qfw-srun -> qfw-teardown
```

| Command | Responsibility |
| --- | --- |
| `qfw-setup` | Select configuration, create run state, validate directory access, and start job-owned services when requested |
| `qfw-status` | Report the current application run and recorded role-manager health |
| `qfw-srun` | Launch the application through DEFw with the prepared runtime and Slurm placement |
| `qfw-teardown` | Stop job-owned services and clean runtime state without stopping site-owned services |

One `qfw-setup` invocation creates one run directory for one logical
application run. Multiple `qfw-srun` steps may share that prepared runtime.
Without `--run-dir`, status, launch, and teardown resolve the current-run
marker written by setup. `qfw-status --json` includes the complete recorded
runtime state and current application-owned manager status. A launcher
managing concurrent runtimes passes each explicit run directory to status,
launch, and teardown because the current marker identifies only the most
recent setup below one run base.

The application process does not call these commands itself. A shell wrapper,
batch script, workflow manager, or Slurm integration owns the lifecycle and
invokes the application through `qfw-srun`.

#### Application Entry Point

The application entry point is a normal executable or Python script. It selects
backend requirements and provides workload inputs such as circuits, shot count,
and algorithm options. It discovers a matching QPM through the environment
prepared by `qfw-setup` and inherited through `qfw-srun`.

Do not launch a QFw Python application with `python my_application.py` after
setup. Use `qfw-srun` so the process runs through `defw-python` and receives the
prepared DEFw and QFw environment.

#### Local Application Run

A local wrapper activates QFw, selects the local profile, runs the application,
and tears down the job-owned services:

```bash
#!/usr/bin/env bash
set -euo pipefail

QFW_PREFIX="${QFW_PREFIX:-$HOME/.local/qfw}"
source "$QFW_PREFIX/bin/qfw-activate" --venv /path/to/qfw-venv

cleanup() {
  local rc=$?
  trap - EXIT
  qfw-teardown || true
  exit "$rc"
}
trap cleanup EXIT

qfw-setup --profile local
qfw-status
qfw-srun my_application.py --shots 128
```

The cleanup trap runs teardown if the application fails or the wrapper is
interrupted. The local profile starts the directory and QPM services selected
by the packaged runtime and service manifest.

#### Site Application Run

On a production cluster, the site module or environment script selects
`site.yaml`. The default runtime uses the existing site directory and
long-running QPM services:

```bash
#!/usr/bin/env bash
set -euo pipefail

source /etc/openqse/qfw/env.sh
source /opt/openqse/qfw/current/bin/qfw-activate \
  --venv "$HOME/venvs/my-qfw-app"

cleanup() {
  local rc=$?
  trap - EXIT
  qfw-teardown || true
  exit "$rc"
}
trap cleanup EXIT

qfw-setup
qfw-status
qfw-srun my_application.py --shots 128
```

Here, `qfw-setup` creates application-side runtime state and verifies the site
directory. It does not start another directory or QPM. `qfw-teardown` removes
only application-owned state and leaves the site services running.

#### Reservation-Managed Run

Under normal use, application code does not call the QPM directly or manage a
reservation ID. The QFw integration layer handles those details:

```text
Application
  -> Qiskit backend, estimator, sampler, or another QFw adapter
    -> QPM execution API
      -> QPM service
```

The application uses its normal framework interface, such as a Qiskit backend
or estimator. The trusted Slurm or site launcher exports
`QFW_RESERVATION_ID` into the application process. The QFw backend or adapter
reads it and attaches it to QPM execution requests.

A trusted Slurm or site driver creates the reservation with the user and
target-device identity. The QPM uses that reservation to select credentials
from its protected device-access configuration. Provider API keys stay inside
the QPM service environment.

Until the production Slurm integration supplies this context,
`qfw_slurm_driver.sh` provides the test workflow. It runs after `qfw-setup`,
creates the reservation, exports `QFW_RESERVATION_ID` to the application step,
launches the application through `qfw-srun`, and releases the reservation.

Normal application code does not read, create, release, or invent reservation
IDs. Direct QPM calls remain available to low-level integrations and new QFw
adapters. Code using that interface is responsible for attaching the
launcher-provided reservation ID, but it still does not create the reservation
itself.

#### Application Output

Applications write normal results to standard output and errors to standard
error. `qfw-srun` launches the application but does not redirect or capture
these streams. They remain attached to the command that invoked `qfw-srun`.

In an interactive run, application output appears in the terminal. In a batch
run, Slurm captures it according to the job's output and error settings. A
wrapper may redirect the streams explicitly when dedicated application log
files are required:

```bash
mkdir -p "$QFW_RUN_BASE_DIR/application-logs"
qfw-srun my_application.py \
  >"$QFW_RUN_BASE_DIR/application-logs/stdout.log" \
  2>"$QFW_RUN_BASE_DIR/application-logs/stderr.log"
```

QFw and DEFw service logs are separate from application output. QFw writes
those service logs under the prepared run directory. A successful application
waits for terminal task results before exiting; submission alone is not a
successful run.

Keep credential files outside the source tree and application run directory.
Restrict them to the account that starts the hardware QPM. Never place API keys
in `site.yaml`, runtime profiles, shell commands, or application logs.

</details>

<details id="deployment">
<summary><strong>7. Choose A Deployment</strong></summary>

The runtime profile describes service ownership and discovery scope. It does
not describe physical placement. In particular, `--profile local` means that
the current job owns its directory and QPM services. Those services may run on
a different node or heterogeneous allocation group from the application.

### Native Workstation

A native workstation has no Slurm allocation. Use the local profile to start
job-owned services. Because only one host is available, the application,
directory service, QPM, and simulator all run on that host:

```bash
qfw-setup --profile local
qfw-srun my_application.py
qfw-teardown
```

The packaged local profile and service manifest provide the starting point.
The selected simulator executable must be available in `PATH`.

### Heterogeneous Slurm Allocation

The local profile also supports a two-group heterogeneous allocation. In this
mode, `local` still means job-owned; it does not mean same-node execution.

```text
Heterogeneous allocation
  Group 0
    -> application launched by qfw-srun
  Group 1
    -> PRTE DVM
    -> job-owned DEFw directory service
    -> selected QPM services
    -> simulator processes launched by those QPM services
```

Request two heterogeneous components using the allocation options required by
the site. A generic Slurm form is:

```bash
salloc \
  --nodes=1 --ntasks=1 \
  : \
  --nodes=1 --ntasks=1
```

Account, partition, walltime, constraint, and network options are site
specific. After the allocation is granted, run the normal local-profile
lifecycle. The run base must be shared by both groups because it carries the
PRTE DVM URI and other coordination state:

```bash
export QFW_RUN_BASE_DIR="/shared/qfw-runs/job-${SLURM_JOB_ID}"
mkdir -p "$QFW_RUN_BASE_DIR"
source "$QFW_PREFIX/bin/qfw-activate" --venv /shared/qfw-venv

qfw-setup --profile local
qfw-srun my_application.py
qfw-teardown
```

QFw detects the heterogeneous Slurm environment. `qfw-setup` uses group 1 for
the PRTE DVM and job-owned quantum service stack. `qfw-srun` uses group 0 for
the application unless `--het-group` explicitly selects another group.

`QFW_RUN_BASE_DIR`, the QFw installation, venv, application, and required
simulator binaries must be available at consistent paths on the nodes where
they are used. A node-local `/tmp` run base is suitable only when every QFw
runtime process executes on the same node.

### QFw Slurm Docker Cluster

Start the cluster by following the QFw-SLURM-Cluster
[container instructions][slurm-cluster].
Inside the controller container, build and install QFw under the shared
workspace:

```bash
cd /workspace/qfw-container-base/QFw
./setup/qfw_install.sh \
  --prefix /workspace/qfw-container-base/qfw-install-dev \
  --with-defw

source /workspace/qfw-container-base/qfw-install-dev/bin/qfw-activate \
  --venv /workspace/qfw-container-base/qfw-shared-test-venv
```

The Docker cluster supports both Slurm layouts. A normal allocation can place
the application and job-owned services on the same node. A heterogeneous
allocation uses the group 0 and group 1 placement described above. In both
cases, use `--profile local` when the allocation owns the directory, DVM, QPM,
and simulator lifecycle.

Use the default site-only runtime when the Docker cluster has an already
running site directory and long-running QPM. In that mode, the application job
does not start or stop those site-owned services.

The checkout, installation, venv, and application files must be visible at the
same paths on every participating container node. Runtime logs and temporary
files may remain node-local unless the selected simulator requires shared
files.

### Production Slurm Cluster

The site administrator starts the site directory and long-running QPM services.
Users activate the shared installation and use the default site-only runtime:

```bash
module load <site-required-modules>
source /opt/openqse/qfw/current/bin/qfw-activate \
  --venv "$HOME/venvs/my-qfw-app"

qfw-setup
qfw-srun my_application.py
qfw-teardown
```

The site installation, user venv, and application must be visible on each
allocated node. `qfw-teardown` cleans only job-owned state. It does not stop
the site directory or long-running QPM services.

</details>

<details id="examples">
<summary><strong>8. Run Examples</strong></summary>

Activate QFw, then enter the installed examples directory:

```bash
source "$QFW_PREFIX/bin/qfw-activate" --venv /path/to/qfw-venv
cd "$QFW_SHARE_DIR/examples"
```

Run the smallest framework check:

```bash
./qfw_init_test.sh
```

Run a Qiskit circuit through NWQ-Sim:

```bash
./qfw_qiskit_simple.sh 4
```

Run a SuperMarQ GHZ circuit through NWQ-Sim:

```bash
./qfw_supermarq.sh sync 1 4 128 false ghz nwqsim
```

Run the compatible examples with application-owned NWQSim services:

```bash
./qfw_run_all.sh --service-mode local --backend nwqsim
```

Run the same examples against an existing site-owned QPM:

```bash
./qfw_run_all.sh \
  --service-mode site \
  --backend nwqsim
```

The example wrappers perform `qfw-setup`, launch the application through
`qfw-srun`, and call `qfw-teardown`. In site mode setup creates application
state with the installed default site-only runtime and does not start services.
In local mode the wrapper selects the installed `local` profile and only its
requested backend. Neither mode generates an application runtime YAML file.
MPI smoke remains a separate test because it has its own placement contract.
Do not deactivate QFw while a wrapper is running.

Examples that require admission-managed capacity use
`qfw_slurm_driver.sh`. It is the test stand-in for the Slurm integration. The
driver reserves capacity, provides `QFW_RESERVATION_ID` to the application,
runs the application, releases the reservation, and records each step.

See [examples/README.md](../examples/README.md) for the complete example list and
wrapper arguments.

</details>

<details id="verification">
<summary><strong>9. Verify A Run</strong></summary>

A successful run must provide more evidence than an exit status. Confirm:

- The setup or driver reports that the runtime and reservation are ready.
- `qfw-status` reports `ready` before the application step begins.
- The application output shows a submitted workload and its result.
- The QPM log shows execution and completion for the same reservation.
- The wrapper or driver reports a successful finish and reservation release.
- `qfw-teardown` removes job-owned tasks while leaving site services running.

Run artifacts are written below `QFW_RUN_BASE_DIR`. On Slurm, also confirm that
the job left no unexpected steps:

```bash
squeue -u "$USER"
```

</details>

<details id="failure-recovery">
<summary><strong>10. Failure Recovery</strong></summary>

Inspect and then tear down an application after a failure:

```bash
qfw-status --json
qfw-teardown
```

Inspect the run directory before deleting it. Application logs, directory
service logs, QPM logs, generated runtime configuration, and driver result
records are stored there.

If setup cannot reach the required directory before its configured timeout, it
returns nonzero and the application should not be launched. Verify the selected
site file, runtime profile, hostname resolution, directory port, allocation,
and firewall policy before retrying.

For stale manager state, missing DVM URIs, failed QPM registration, leaked
reservations, and clean service restart, follow the
[service recovery recipe](recipes/recover-services.md). Preserve the manager
run directory until the recorded node, PID, ownership, and logs have been
inspected.

When finished, restore the shell environment with:

```bash
qfw-deactivate
```

</details>

## Further Reading

- [README.md](README.md) provides the complete project overview.
- [examples/README.md](examples/README.md) documents example arguments.
- [docs/detailed-design.md](docs/detailed-design.md) defines runtime and
  configuration behavior.
- [docs/test-plan.md](docs/test-plan.md) defines system validation and
  acceptance criteria.

[slurm-cluster]: https://github.com/openQSE/QFw-SLURM-Cluster#readme
