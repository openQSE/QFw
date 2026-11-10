# Run QFw Against Amazon Braket

This recipe runs the QFw shim QPM against Amazon Braket devices through the
QDMI library: the SV1 state-vector simulator and Rigetti's Cepheus-1-108Q
QPU. It covers what the AWS account must have before QFw can create a task,
how the service process gets its AWS identity, how a device is described,
and how to run and read the shim smoke and a Qiskit program.

The route described here is an application-owned service plane started from
a QFw installation, which is how both devices were verified. Site-owned shim
services for Braket devices need the same environment in the service
process and are a site configuration matter (see Step 7).

Run `man 1 qfw-activate` and `man 1 qfw_qiskit_simple.sh` inside the
cluster for command details, and `./qfw_shim_smoke.sh --help` for the smoke's
options. Section 8 and Section 13 of
[the QPU frontend contract](../design/qpu-frontend-contract.md) describe the
Braket profile this recipe exercises.

## What you need

- A QFw installation whose Python environment carries `amazon-braket-qdmi`
  (the cluster image and `do_qfw_build.sh` pin it through
  `setup/qdmi-requirements.txt`) beside `mqt-core`.
  Check with `python -c 'import amazon.braket.qdmi'` after activation.
- An AWS account prepared as in Step 1, and an identity for the service
  process as in Step 2.
- Network access from the node that runs the shim service to the Braket and
  S3 endpoints of the device's Region. The library talks to AWS directly.

Every task on a Braket device is billed. Prices as of October 2026: SV1 is
$0.075 per minute with a 3 second minimum and one free hour per month for
the first year; Cepheus-1-108Q is $0.30 per task plus $0.000425 per shot.
Check the Braket pricing page before a campaign.

## 1. Prepare the AWS account (once)

1. **Use a classic account.** The sign-up AWS introduced in 2026 puts a new
   account in a "project" that cannot run Braket at all (unsupported
   service, Region fixed by country, no IAM console). Choose "Sign up for
   AWS (advanced)", or activate advanced features on an existing project.
2. **Create the identity QFw's service will use.** An IAM user with the
   managed policy `AmazonBraketFullAccess` and an access key is the simplest
   fit for a cluster. An SSO or `credential_process` profile works too,
   since the library uses the AWS SDK's default credential chain.
3. **Create Braket's service-linked role.** Open the Braket console's
   *Permissions and settings* page (Execution roles tab) and let it create
   `AWSServiceRoleForAmazonBraket`. Opening the Devices page does not create
   it. Without the role, `CreateQuantumTask` is denied for every device, and
   QFw only sees "Checking job status: Permission denied" (see Step 6).
4. **Enable third-party devices and set limits.** Accept the third-party
   device terms, set a spending limit per QPU (the console shows a QPU only
   in its own Region; Cepheus-1-108Q is in us-west-1), and create a budget
   alert. SV1 is available in us-east-1, us-west-1, us-west-2 and
   eu-west-2.
5. **Create a results bucket per Region.** Name it `amazon-braket-<name>`,
   which `AmazonBraketFullAccess` covers, in the Region of the device that
   writes to it: us-east-1 for SV1, us-west-1 for Cepheus. The device entry
   names it (Step 3). Without one, the library creates a default bucket,
   which needs S3 permissions the Braket policy alone does not grant.

## 2. Give the service process its AWS identity

The Braket QDMI library authenticates through the AWS SDK's default
credential chain inside the service process. QFw holds no AWS secret, so
nothing AWS-related goes into a QFw device-access or credential file. A key
in a tracked configuration file is exactly the leak this design avoids.

Put the profile in two files outside every repository and outside the QFw
configuration directories, readable by the service's user only:

```ini
# /secure/path/aws/config
[profile qfw]
region = us-east-1
output = json
```

```ini
# /secure/path/aws/credentials  (mode 600; never print or commit it)
[qfw]
aws_access_key_id = ...
aws_secret_access_key = ...
```

Point the SDK at them in the environment the shim service inherits:

```bash
export AWS_SHARED_CREDENTIALS_FILE=/secure/path/aws/credentials
export AWS_CONFIG_FILE=/secure/path/aws/config
export AWS_PROFILE=qfw
export AWS_EC2_METADATA_DISABLED=true
chmod 600 /secure/path/aws/credentials /secure/path/aws/config
```

The example wrappers copy the caller's environment into the services they
start, so exporting these in the allocation is enough for this recipe. A
site-owned service does not inherit a login environment (ssh drops it), so
a site puts the same variables where its services are activated.

On the QFw side the device's `credential-provider` carries no key either.
The example config uses the `no-secret` provider, which is right for a
smoke. A site plane uses a provider of type `entitlement`, which checks that
the user and the device are enabled in the credential database and binds
nothing else:

```yaml
credential-providers:
  aws-site-identity:
    type: entitlement
    credential-db: /etc/openqse/qfw/device/qpu-users.json
```

## 3. Describe the device

`examples/qfw_shim_device_access.yaml` ships two Braket entries, `aws-sv1`
and `aws-cepheus`. A site copies the shape into its own device-access file
and sets the results bucket:

```yaml
qpus:
  aws-cepheus:
    provider: aws
    provider-device-id: arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q
    qdmi-device-id: amazon.braket.rigetti.cepheus-1-108q
    aws-region: us-west-1
    s3-results-uri: s3://amazon-braket-<your-bucket>/qfw/cepheus
    url: https://braket.us-west-1.amazonaws.com
    num-qubits: 108
    max-shots: 1024
    job-timeout-seconds: 7200
    credential-provider: shim-no-secret
    libraries:
      - qdmi
    preference: qdmi
    execution-owner: qdmi
```

| Key | Meaning |
| --- | --- |
| `provider-device-id` | The Braket device ARN. The library opens one session per ARN. |
| `qdmi-device-id` | The id the device is registered under in the QDMI driver. Use the library's catalogue name (`amazon.braket.sv1`, `amazon.braket.rigetti.cepheus-1-108q`, ...), listed in `amazon-braket-qdmi-device.qdmi.json` under the package's `data` directory. |
| `aws-region` | The device's Region. It defaults to the Region in the ARN, which SV1's ARN does not carry. |
| `s3-results-uri` | Where task results land. The bucket must be in `aws-region` and should be named `amazon-braket-*`. |
| `reservation-arn` | Optional. A Braket reservation ARN, which also makes the device report available outside public execution windows. |
| `url` | Ignored by the library, required by QFw's device selection. |
| `num-qubits` | Overrides the qubit count QFw reports for the device. The device's own count still appears in `get_device_info`. |
| `max-shots` | Refused above before a task exists. Keep it on billed devices. |
| `job-timeout-seconds` | How long a queued task is waited for before QFw cancels it. QPUs queue; 300 seconds is the default. The example gives Cepheus two hours, since a prioritized hybrid job can hold the QPU for an hour inside a window. Set it past the next execution window to submit across a gap (Step 4). |

Measurements are keyed by classical bit, as in Qiskit, whichever device ran
them. The shim writes its OpenQASM 3 so that Braket's backends, which order
result columns differently, return identical rows. Section 8 of the frontend
contract explains the mechanism.

## 4. Check the device without spending anything

Run these in the `slurmctld` container inside an allocation, with the Step 2
variables exported. Device queries are free; only tasks are billed. If the
site's Slurm requires an account, export `SALLOC_ACCOUNT` and
`SBATCH_ACCOUNT` as well.

```bash
export QFW_SHARED_ROOT=/workspace/qfw-container-base
export QFW_RUN_BASE_DIR="${QFW_SHARED_ROOT}/qfw-runs"
mkdir -p "${QFW_RUN_BASE_DIR}"
source /opt/openqse/qfw/bin/qfw-activate --venv /opt/openqse/qfw-venv
cd "${QFW_SHARE_DIR}/examples"

./qfw_shim_smoke.sh --device-id aws-sv1 --lib qdmi --call test
./qfw_shim_smoke.sh --device-id aws-sv1 --lib qdmi --call get_device_info
./qfw_shim_smoke.sh --device-id aws-cepheus --lib qdmi --call get_device_info
```

`get_device_info` reports 34 qubits for SV1 and the QPU's current count for
Cepheus (107 of its 108 at the time of writing). A failure here is an
account or network problem, not a circuit problem.

A QPU is only usable inside its execution windows. The library reports that
as the QDMI device status, which QFw's device record does not carry. Ask
the library directly before paying for a task:

```bash
python - <<'PY'
from amazon.braket.qdmi.qiskit import AmazonBraketBackend

backend = AmazonBraketBackend(
    device_id="amazon.braket.rigetti.cepheus-1-108q",
    device_arn="arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q",
    region="us-west-1")
print(backend.device.status())
PY
```

This is the same packaged adapter QFw's Braket profile opens the device
with (`amazon-braket-qdmi[qiskit]`, MQT Core 4.2).

`Status.IDLE` means the device is inside an execution window with fewer
than five tasks queued. `Status.BUSY` means a longer queue or a closed
execution window, which the library does not tell apart; the Braket
console's device page lists the windows, in UTC, as does `GetDevice`'s
`service.executionWindows`. `Status.MAINTENANCE` is Braket's OFFLINE. A
task sent outside a window waits in Braket's queue until the window opens
or QFw's `job-timeout-seconds` passes, whichever comes first; QFw then
cancels it, and a task cancelled before it ran is not billed.

Cepheus-1-108Q's windows, as `GetDevice` reported them on 2026-10-15, are
every day from 00:00 to 01:59, 04:00 to 13:59 and 16:00 to 23:59 UTC, so
the device is away for two hours twice a day. Windows change, so read the
current ones from the console's device page, or with the AWS CLI where it
is installed:

```bash
aws braket get-device --region us-west-1 \
  --device-arn arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q \
  --query deviceCapabilities --output text \
  | python -c 'import json, sys; print(json.load(sys.stdin)["service"]["executionWindows"])'
```

A task submitted in the 02:00 gap with a 30 minute wait sat queued and
was cancelled by QFw, unbilled. Resubmitted twelve minutes before 04:00,
the same task ran within the first minute of the window. Inside a window,
`BUSY` can also mean a long wait: with a prioritized hybrid job running on
Cepheus and eleven tasks queued, a task waited an hour before it ran, so a
30 minute timeout cancelled it and a two hour one covered it. To run
across a gap or behind a hybrid job, set `job-timeout-seconds` past the
expected wait and give the smoke's `--circuit-run-timeout` at least the
same.

## 5. Run circuits

The smoke's `async_run` submits one task. Its default circuit is a single X,
whose counts should read `'1'`; `--circuit asymmetric` measures three qubits
into permuted classical bits and should read `'100'`, which proves the
result's bit order on a new device. Give a QPU a client-side wait longer
than its `job-timeout-seconds`:

```bash
./qfw_shim_smoke.sh --device-id aws-sv1 --lib qdmi --call async_run --shots 10
./qfw_shim_smoke.sh --device-id aws-sv1 --lib qdmi --call async_run \
  --shots 100 --circuit asymmetric
./qfw_shim_smoke.sh --device-id aws-cepheus --lib qdmi --call async_run \
  --shots 100 --circuit asymmetric --circuit-run-timeout 7300
```

Real hardware is noisy. The smoke's counts check accepts the expected key as
the most frequent one; on Cepheus the asymmetric circuit read `'100'` in 77
of 100 shots.

A Qiskit program selects the library per job, through `lib="qdmi"` in
`QFwBackend.run()`, `backend.options.lib`, or `QFW_SHIM_LIB` in the
environment, and runs against a shim QPM that is bound to the device. With a
site-owned shim service for the device:

```bash
QFW_SHIM_LIB=qdmi ./qfw_qiskit_simple.sh --service-mode site --backend shim 3
```

The GHZ example runs 1024 shots, about $0.74 on Cepheus. The `max-shots`
cap in the device entry refuses anything larger before a task is created.
On an application-owned plane the smoke harness is the client; the example
wrappers' local mode starts simulator services only.

## 6. Read a failure

QFw reports the error text MQT Core gives it, which for a denied AWS call is
only `QDMI job.check() failed: Checking job status: Permission denied`. The
library submits in the background and surfaces the real cause at the first
status check, on the service's standard error, as
`<operation> failed (<AWSException>): <message>`. The smoke deletes its run
directory at teardown, so keep it and read the service's log:

```bash
QFW_EXAMPLE_KEEP_RUN_DIR=1 \
QFW_EXAMPLE_LOG_ARCHIVE_DIR="${QFW_RUN_BASE_DIR}/aws-debug" \
  ./qfw_shim_smoke.sh --device-id aws-sv1 --lib qdmi --call async_run --shots 10
grep -r 'failed (' "${QFW_RUN_BASE_DIR}/aws-debug"
```

| Symptom | Cause |
| --- | --- |
| `get_device_info` fails with "Permission denied" | No credentials reach the service, the profile name is wrong, or the node cannot reach AWS. |
| `CreateQuantumTask failed (AccessDeniedException): AWSServiceRoleForAmazonBraket role doesn't exist` | Step 1.3 was skipped. Create the role from the Braket Permissions page. |
| `CreateQuantumTask` denied, or an S3 error, with the role in place | The results bucket is missing, is in another Region, or is not named `amazon-braket-*`; or no bucket is configured and the policy does not allow creating one. |
| The task sits queued, then QFw reports it timed out and was cancelled | The device is outside its execution window (status `BUSY`, windows in UTC) or in maintenance, or the queue is longer than `job-timeout-seconds`. |
| `<n> shots is over the <cap>-shot cap for device ...` | The request is above the entry's `max-shots`. Raise the cap deliberately or lower the shots. |
| "the device accepts none of [...]" | A gate the device does not take and QFw could not decompose. Check the device's operation set. |

## 7. What the site plane adds

A site-owned shim service for a Braket device is one `qfw-qpm-svc(1)`
instance per device, bound to the device entry, with the Step 2 variables
present in the process it runs in. Nothing else differs from the local
route above: the same entry, the same `entitlement` provider for access
control, the same smoke and Qiskit commands with `--service-mode site`.
When the cluster's telemetry stack is enabled, Qiskit programs appear on the
QFw Jobs dashboard as application jobs; the smoke's circuits appear only on
the service side, in the QPM's traces and logs, because the smoke is not an
application.
