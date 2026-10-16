---
type: markdown
---

# QFw Shim Machine Support Guide

This guide describes how to add support for a new quantum machine through the
QFw shim path:

```text
QFw client/backend -> QPM API -> svc_lib_qpm -> Frontend -> QRMI or QDMI -> machine provider
```

The intended boundary is:

- Workflow managers request a public QPU name through qfw-slurm.
- qfw-slurm reserves the QPM service that owns that QPU name.
- QFw launches the workload against the reserved QPM service.
- The shim translates QFw/QPM calls into provider-specific QRMI or QDMI calls.

Workflow managers, qfw-slurm, and application code should not need
provider-specific branches. New machine behavior should live in the QRMI
resource implementation or QDMI device implementation selected by the shim.

## What Machine Support Means

A new machine is supported when the following pieces work together:

| Piece | Responsibility |
| --- | --- |
| Device descriptor | Names the logical machine, provider, endpoint, QRMI/QDMI library choice, capabilities, and non-secret provider metadata. |
| Credential source | Resolves the submitting user to the provider credentials needed for that machine. |
| QPM service | Runs `svc_lib_qpm` for one logical device and registers it with the QFw directory. |
| Provider adapter | Converts QFw/QPM calls into provider-specific QRMI or QDMI calls and normalizes results back to QFw shapes. |
| qfw-slurm mapping | Exposes a public `--qpu` name that maps to the QPM service. |
| Tests and smoke runs | Prove descriptor parsing, credential resolution, adapter behavior, service startup, and qfw-slurm launch. |

## Main Implementation Points

Use these files as the starting map when adding a machine:

| Area | Starting point |
| --- | --- |
| Device-access parsing | `../QFw/services/util/device_access.py` |
| Descriptor resolution | `../QFw/services/svc_lib_qpm/descriptor.py` |
| QPM frontend routing | `../QFw/services/svc_lib_qpm/frontend.py` |
| QRMI driver | `../QFw/services/svc_lib_qpm/drivers/qrmi_driver.py` |
| QDMI driver | `../QFw/services/svc_lib_qpm/drivers/qdmi_driver.py` |
| Shim service behavior | `../QFw/services/svc_lib_qpm/svc_qpm.py` |
| Service manifests | `/opt/openqse/qfw/share/qfw/config/services/site-services.yaml` |
| qfw-slurm resource mapping | `/etc/openqse/qfw-slurm/resources.lua` and `/etc/openqse/qfw-slurm/plugin.conf` |

## Step 1. Choose QRMI or QDMI

Start by deciding which lower-level library owns the machine integration.

Use QRMI when:

- QRMI exposes a resource type for the provider or can be extended to do so.
- The provider has a task-style API for submitting circuits, polling status,
  retrieving results, and collecting metadata.
- Provider credentials can be passed through environment variables, resource
  construction arguments, or another QRMI-supported mechanism.

Use QDMI when:

- A provider QDMI/FoMaC device library exists.
- The device can be opened through MQT Core QDMI.
- QDMI exposes enough information for the QFw contract: execution, topology,
  calibration or device metadata, task state, and results.

If both are available, prefer the path that gives the cleanest provider
contract with the least provider-specific logic in QFw. Do not enable both in
the descriptor until both paths have been tested.

## Step 2. Define the Logical Machine

The installed site configuration is `/etc/openqse/qfw/site.yaml`. That file
selects the device-access file used by the service plane. In the current
cluster setup, `/etc/openqse/qfw/site.yaml` contains:

```yaml
service:
  device-access-config: /etc/openqse/qfw/device/device-access.yaml
```

Add the logical machine entry to the file named by `device-access-config`. In
the current cluster setup, that is
`/etc/openqse/qfw/device/device-access.yaml`.

Use stable internal names. A good pattern is:

- public qfw-slurm name: `xyz-qpu`
- QFw service name: `shim-xyz-qpu`
- provider device ID: whatever the provider API expects

Example QRMI-backed machine entry in
`/etc/openqse/qfw/device/device-access.yaml`:

```yaml
qpus:
  xyz-qpu:
    provider: xyz
    provider-device-id: xyz_machine_1
    resource-type: XYZQuantumService
    url: https://provider.example/api/v1
    credential-db: /etc/openqse/qfw/device/qpu-users.json
    libraries:
      - qrmi
    preference: qrmi
    execution-owner: qrmi
    caps:
      run_circuit:
        - qrmi
      get_task_timing:
        - qrmi
      get_task_metadata:
        - qrmi
```

The `credential-db` line names the file that contains per-user provider
credentials for this device.

If the provider supports topology or calibration calls, add those capabilities
only after the adapter implements and tests them:

```yaml
caps:
  run_circuit: [qrmi]
  get_task_timing: [qrmi]
  get_task_metadata: [qrmi]
  get_device_info: [qrmi]
  get_coupling_graph: [qrmi]
  get_calibration_snapshot: [qrmi]
```

For QDMI-backed machines, use `qdmi` in `libraries`, `preference`, and the
capability map only after the provider QDMI path can open a real device session.

Amazon Braket devices are QDMI-backed machines with their own keys
(`qdmi-device-id`, `aws-region`, `s3-results-uri`, `max-shots`, and so on).
The [Amazon Braket recipe](recipes/run-against-aws-braket.md) lists them with
a complete entry.

## Step 3. Add Provider Credentials

Credentials should be resolved per user and per device. Secrets belong in the
credential database or credential provider, not in the device descriptor.

In the current installed cluster setup, the file-backed credential database is
`/etc/openqse/qfw/device/qpu-users.json`. This is the file selected by the
`credential-db: /etc/openqse/qfw/device/qpu-users.json` line in the Step 2
device-access entry.

Example record in `/etc/openqse/qfw/device/qpu-users.json`:

```json
{
  "users": {
    "alice": {
      "enabled": true,
      "devices": {
        "xyz-qpu": {
          "enabled": true,
          "api_key": "REDACTED"
        }
      }
    }
  }
}
```

If the provider needs extra secret material, keep it under the same user/device
entry:

```json
{
  "api_key": "REDACTED",
  "project_id": "REDACTED",
  "client_secret": "REDACTED"
}
```

Production sites may use a credential-provider plugin instead of a file-backed
database. The logical contract should stay the same: QPM resolves a
reservation-bound credential for the selected user and machine, and the provider
adapter receives only the credential material it needs.

### IQM

For IQM, store each user's API token in the `api_key` field shown above.
The native IQM service and the shim's QRMI and QDMI paths use the same
device-access settings and per-user credentials.

### Amazon Braket

Amazon Braket devices are the exception to the per-user key. The Braket QDMI
device library authenticates through the AWS SDK's own credential chain in the
service process, so QFw holds no AWS secret, and none belongs in the
device-access file or the credential database. Use the `no-secret` provider
for a smoke, or a provider of type `entitlement` on a site plane, which checks
that the user and the device are enabled without binding a key. The
[Amazon Braket recipe](recipes/run-against-aws-braket.md) covers the account,
the credential files, and the environment the service needs.

### IBM

IBM QDMI 1.0 supports IBM Cloud Qiskit Runtime and requires Python 3.11 or
newer. Install the provider packages from `setup/qdmi-requirements.txt`, then
configure an IBM backend:

```yaml
qpus:
  ibm-torino:
    provider: ibm
    provider-device-id: ibm_torino
    url: https://quantum.cloud.ibm.com/api/v1
    credential-db: /etc/openqse/qfw/device/qpu-users.json
    libraries: [qdmi]
    preference: qdmi
    execution-owner: qdmi
```

Under each user's `devices.ibm-torino` entry in the credential database, set:

```json
{
  "enabled": true,
  "api_key": "REDACTED",
  "service_crn": "REDACTED"
}
```

The API key and service-instance CRN are bound to the reservation. A device's
`service-crn` can provide a default CRN when the user record omits it. Set
`iam-endpoint` only when using a custom IAM endpoint. QFw uses the packaged
`IBMBackend` to open the session and accepts circuits in QPY format through
the existing shim service. Bind all parameters and use classical registers
that cover each classical bit once, in bit order.

## Step 4. Add a QPM Shim Service

Add a service entry that uses the shim module and points at the logical machine.
In the default installed cluster image, the service manifest lives in:

```text
/opt/openqse/qfw/share/qfw/config/services/site-services.yaml
```

The installed site config `/etc/openqse/qfw/site.yaml` selects that file with
this setting:

```yaml
service:
  manifest: /opt/openqse/qfw/share/qfw/config/services/site-services.yaml
```

Add the new service entry to the `services` list in
`/opt/openqse/qfw/share/qfw/config/services/site-services.yaml`:

```yaml
services:
  - name: shim-xyz-qpu
    credential-mode: required
    module: svc_lib_qpm
    load-modules: svc_lib_qpm,api_launcher
    agent-prefix: qpm_shim_xyz
    device-id: xyz-qpu
    listen-port: 8690
    telnet-port: 8691
    provider-launch:
      type: qrmi-qdmi
```

The key fields are `module: svc_lib_qpm`, `credential-mode`, and `device-id`.
The device descriptor decides whether calls route to QRMI, QDMI, or both. The
service name is what the QFw directory advertises and what qfw-slurm maps to.

## Step 5. Confirm the QRMI Resource or QDMI Device

Machine-specific behavior belongs in the selected QRMI resource implementation
or QDMI device implementation. The QFw shim should consume the QRMI or QDMI
contract and remain provider-agnostic.

If adding a machine requires machine-specific changes in the shim, treat that
as an architecture gap to clean up separately, not as normal machine onboarding.

Do not put provider branches in workflow-manager code, qfw-slurm, QFw client
libraries, or the shim service. Those layers should continue to submit a
workload to QPM using a reservation and a public QPU name.

## Step 6. Expose the Machine to qfw-slurm

After the QPM service starts and registers correctly, expose it to Slurm users
with a public resource mapping.

Add the public QPU mapping to `/etc/openqse/qfw-slurm/resources.lua`:

```lua
return {
    resources = {
        ["xyz-qpu"] = "shim-xyz-qpu",
    },
    partitions = {
        normal = {
            allowed = {
                ["xyz-qpu"] = true,
            },
        },
    },
}
```

Add the native plugin resource mapping to `/etc/openqse/qfw-slurm/plugin.conf`:

```ini
[resource "xyz-qpu"]
service_id=shim-xyz-qpu
```

After that, users and workflow managers request:

```bash
#SBATCH --qpu=xyz-qpu
```

They should not need the provider name, QRMI resource type, endpoint, credential
layout, or adapter details.
