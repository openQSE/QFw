# Configure a Site-owned QPM

A site-owned QPM runs independently of application allocations. The
administrator selects the service hosts and starts each manager on the host
that owns it. No Slurm allocation variables participate in this procedure.

Applications connect through the published directory connection file. Their
teardown never stops these site-owned processes.

## 1. Configure the service environment

Install the same QFw release and Python environment on every service host.
Select the installation, virtual environment, and `site.yaml` configuration
before activating QFw. The `qfw-site.yaml(5)` manual explains its fields.
After activation, open that reference with `man 5 qfw-site.yaml`. Adjust these
example paths for the site:

`qfw-activate(1)` prepares the shell for an installed QFw environment. Run
`man 1 qfw-activate` for its activation and virtual-environment rules.

```bash
# These values select the activation script and Python environment.
# qfw-activate preserves QFW_SITE_CONFIG as the site.yaml override.
export QFW_INSTALL_PREFIX=/opt/openqse/qfw/current
export QFW_VENV=/opt/openqse/qfw/venv
export QFW_SITE_CONFIG=/etc/openqse/qfw/site.yaml

source "${QFW_INSTALL_PREFIX}/bin/qfw-activate" --venv "${QFW_VENV}"

# Shared root for runtime artifacts exchanged across nodes. Every service and
# application node must mount this directory at the same pathname.
export QFW_SHARED_ROOT=/shared/openqse/qfw
```

`qfw-activate` sets `QFW_PREFIX` to the installation containing the activation
script and selects the configured DEFw installation as `DEFW_PREFIX`.
The service managers expand those variables and `QFW_SHARED_ROOT` when they
load the selected `site.yaml`.

`QFW_SHARED_ROOT` holds runtime artifacts that must cross node boundaries. The
packaged `site.yaml` places the directory-service connection record here:

```text
${QFW_SHARED_ROOT}/qfw-site-services/directory-service.json
```

The record contains the directory service hostname and port. Every QPM and
application client reads it before connecting. DVM-backed simulator services
may also place their DVM URI and shared runtime state beneath this root.

The directory host, QPM hosts, simulator nodes, and application nodes must
therefore mount `QFW_SHARED_ROOT` at the same pathname. QFw binaries, the
Python environment, `site.yaml`, and provider credentials use the separate
paths configured above. A permanent `directory-service.endpoint` removes the
shared connection-file requirement, although a DVM-backed simulator may still
require shared runtime storage.

## 2. Select the service hosts

Choose a host for the directory service and a host for each QPM. Install or
invoke the corresponding manager on those hosts. For example, a central
service node may own the directory while the IQM system head node owns the IQM
QPM.

The host is determined by where the manager runs. A site manager started
outside Slurm defaults to the local hostname, so the canonical commands omit
`--node`. Remote placement through `--node` is available only within a Slurm
allocation and belongs to allocation-owned testing.

## 3. Start the directory service

The directory manager stores its process state, readiness record, and logs in
one run directory. This directory can use storage local to the directory host:

```bash
export DIR_RUN_DIR=/var/lib/qfw/directory
mkdir -p "${DIR_RUN_DIR}"
```

`qfw-dir-svc(1)` manages one directory-service instance. Run
`man 1 qfw-dir-svc` for its commands, options, state files, and exit status.
On the selected directory host, run:

```bash
qfw-dir-svc start \
  --scope site \
  --run-dir "${DIR_RUN_DIR}" \
  --site-config "${QFW_SITE_CONFIG}"

qfw-dir-svc status --run-dir "${DIR_RUN_DIR}"
```

The connection file now contains the directory service hostname and port.
When a QPM manager starts, it reads the `directory-service.connection-file`
path from the same `site.yaml`, loads the address from that JSON record, and
connects to the directory service.

## 4. Start a long-running QPM

The `service.manifest` field in `site.yaml` identifies the service manifest.
Each entry in that manifest has a `name`. This name is the service ID passed to
the QPM manager. For example, this manifest entry defines the service ID
`fake-iqm`:

```yaml
# /etc/openqse/qfw/services/site-services.yaml
services:
  - name: fake-iqm
    module: svc_fake_iqm_qpm
```

Set `QPM_SERVICE_ID` to the exact `name` of the QPM to start.
`QPM_SERVICE_ID` is a shell variable used by this recipe, not a QFw
configuration field:

```bash
export QPM_SERVICE_ID=fake-iqm
export QPM_RUN_DIR="/var/lib/qfw/qpm/${QPM_SERVICE_ID}"
mkdir -p "${QPM_RUN_DIR}"
```

The selected QPM keeps its process state, readiness record, and logs in
`QPM_RUN_DIR`. Provider QPMs that do not use a DVM can keep this directory on
storage local to the QPM host.

`qfw-qpm-svc(1)` manages one QPM instance. Run `man 1 qfw-qpm-svc` for its
commands, options, service selection, and state files. The service manifest is
documented by `qfw-services.yaml(5)`; run `man 5 qfw-services.yaml` for its
fields. On the selected QPM host, run this after the directory reports ready:

```bash
qfw-qpm-svc start \
  --scope site \
  --run-dir "${QPM_RUN_DIR}" \
  --site-config "${QFW_SITE_CONFIG}" \
  --service-id "${QPM_SERVICE_ID}"

qfw-qpm-svc status --run-dir "${QPM_RUN_DIR}"
```

A hardware QPM's service-manifest entry contains a logical `device-id`. The
`service.device-access-config` field in `site.yaml` selects a site-owned YAML
file that maps this ID to the provider, provider device name, API endpoint, and
credential-provider reference. This keeps site-specific device and security
configuration separate from the reusable service manifest. Only the QPM
service account needs read access to this file and its referenced credential
store.

For example, a service-manifest entry with `device-id: site-qpu` can use these
two configuration fragments:

```yaml
# site.yaml
service:
  device-access-config: /etc/openqse/qfw/device/device-access.yaml
```

```yaml
# /etc/openqse/qfw/device/device-access.yaml
qpus:
  site-qpu:
    provider: iqm
    provider-device-id: default
    url: https://provider.example/
    credential-db: qpu-users.json
```

Run `man 5 qfw-device-access.yaml` for the complete file format and security
requirements.

Services with an internal or remote-API provider do not need a PRTE DVM. A
DVM-backed QPM uses the same manager command with the additional shared-storage
and runtime configuration described below.

## 5. Add DVM-backed NWQSim configuration

NWQSim requires an administrator-selected simulator pool. Define a fixed host
list in the QPM service environment rather than deriving it from a Slurm job:

```bash
export QFW_SIMULATOR_NODES=sim01,sim02,sim03
export QPM_RUN_DIR=/shared/openqse/qfw/site-services/nwqsim
export SERVICE_RUNTIME_CONFIG=/etc/openqse/qfw/nwqsim-runtime.yaml
```

The site service manifest should assign the same fixed pool to NWQSim. An
environment reference keeps the site-specific host list outside the installed
manifest:

```yaml
# /etc/openqse/qfw/services/site-services.yaml
services:
  - name: nwqsim
    module: svc_nwqsim_qpm
    load-modules: svc_nwqsim_qpm,api_launcher
    environment-modules:
      - libfabric
      - nwqsim
    required-executables:
      - circuit_runner.nwqsim
    agent-prefix: qpm_nwqsim
    assigned-hosts: ${QFW_SIMULATOR_NODES}
    assigned-hosts-env: QFW_QPM_ASSIGNED_HOSTS
    listen-port: 8490
    telnet-port: 8491
    provider-launch:
      type: mpi
      wrapper: null
      use-dvm: true
```

Point `service.manifest` in `site.yaml` to that site-owned manifest. Configure
PRTE with the same explicit nodes:

```yaml
# /etc/openqse/qfw/nwqsim-runtime.yaml
resolver:
  scope-order:
    - site
local-services:
  start-prte: true
  prte:
    hosts: ${QFW_SIMULATOR_NODES}
```

Run the QPM manager on the selected NWQSim service host:

```bash
qfw-qpm-svc start \
  --scope site \
  --run-dir "${QPM_RUN_DIR}" \
  --site-config "${QFW_SITE_CONFIG}" \
  --runtime-config "${SERVICE_RUNTIME_CONFIG}" \
  --service-id nwqsim
```

`QPM_RUN_DIR` must be visible at the same pathname on every simulator node.
The PRTE process writes its DVM URI there. TNQVM may use the same explicit
placement model, but it is not part of the release validation gate.

## 6. Run under a service supervisor

Use `run` instead of `start` under systemd or another foreground supervisor.
The installed `qfw-dirsvc@.service` and `qfw-qpm@.service` templates start each
manager on the machine where its unit is enabled. They directly support real
IQM and fakeIQM, which use node-local manager state and no DVM.

NWQSim needs a site drop-in that supplies `--runtime-config` and replaces the
template's node-local run directory with the shared `QPM_RUN_DIR`. Its service
environment must also define `QFW_SIMULATOR_NODES`.

SIGTERM stops only the component owned by that manager.

## 7. Stop the services

Stop each QPM on its manager host before stopping the directory service:

```bash
qfw-qpm-svc stop --run-dir "${QPM_RUN_DIR}"
qfw-dir-svc stop --run-dir "${DIR_RUN_DIR}"
qfw-deactivate
```

`qfw-deactivate(1)` restores the shell environment saved during activation.
Run `man 1 qfw-deactivate` for details. Application-side `qfw-teardown(1)` is
not a site-service operation; its application cleanup contract is documented
by `man 1 qfw-teardown`.

<details>
<summary>Diagnostics and verification</summary>

`qfw-status(1)` reports an application-owned runtime. Run `man 1 qfw-status`
for its output and run-directory selection rules.

```bash
command -v qfw-dir-svc qfw-qpm-svc qfw-status prte pterm
printf 'host=%s\nQFW_PREFIX=%s\nVIRTUAL_ENV=%s\n' \
  "$(hostname -s)" "${QFW_PREFIX}" "${VIRTUAL_ENV}"
```

On the directory host, inspect `${DIR_RUN_DIR}/state/service-plane.json`. On a
QPM host, inspect `${QPM_RUN_DIR}/state/service-plane.json` and the readiness
files and logs below that run directory. Generated state should record the
local manager host as the service target.

Use [Service recovery](recover-services.md) for stale state, failed
registration, or a missing DVM URI.

</details>

<details>
<summary>Configuration file reference</summary>

The paths below match the examples in this recipe. A site may choose different
paths while preserving the same ownership and visibility rules.

| Example path | Read by | Purpose | Placement and protection |
| --- | --- | --- | --- |
| `/etc/openqse/qfw/site.yaml` | Service managers and application clients | Selects installation prefixes, directory discovery, the service manifest, the device-access file, and common QPM policy. See `qfw-site.yaml(5)`. | Deploy the same contents at the same path on service and application nodes. This file contains paths, not provider secrets. |
| `/etc/openqse/qfw/services/site-services.yaml` | QPM managers | Defines available QPM service IDs, implementation modules, ports, provider launch types, and placement inputs. See `qfw-services.yaml(5)`. | Install on each QPM manager host. It may be site-specific but does not contain provider credentials. |
| `/etc/openqse/qfw/device/device-access.yaml` | Device-backed QPM processes | Maps a manifest `device-id` to a provider, provider device name, API endpoint, and credential-provider reference. See `qfw-device-access.yaml(5)`. | Keep on the QPM host and restrict access to the QPM service account. |
| `/etc/openqse/qfw/device/qpu-users.json` | A QPM using the file credential provider | Supplies user- and device-specific provider secrets referenced by `credential-db`. The relative `qpu-users.json` example resolves beside `device-access.yaml`. | Keep on the QPM host with permissions limited to the QPM service account. A site credential plugin may replace this file. |
| `/etc/openqse/qfw/nwqsim-runtime.yaml` | The NWQSim QPM manager | Selects site directory resolution and enables PRTE on the administrator-selected simulator hosts. See `qfw-runtime.yaml(5)`. | Install on the NWQSim manager host. The simulator nodes need the shared QPM run directory, not this configuration file. |

`${QFW_SHARED_ROOT}/qfw-site-services/directory-service.json`, manager state,
readiness records, logs, and the PRTE DVM URI are generated runtime artifacts.
They are not configuration inputs. The directory connection record and any
DVM coordination files must be visible to every node that reads them.

</details>
