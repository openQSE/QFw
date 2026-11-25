# Pulser Programs on Pasqal Devices — Design

Status: **Draft**
Author: Aleksander Wennersteen
Date: 2026-10-19

## 1. Purpose

A QPM so far runs gate circuits. A Pasqal neutral-atom device runs a Pulser
sequence: a register of atoms and the pulses applied to it. This document
describes how a QPM takes such a program, how a client finds a QPM that runs
one, how the shim runs it on Pasqal devices through QRMI, and the Pulser
client that submits it.

It builds on the [QPU front-end contract](qpu-frontend-contract.md): the
program travels in the existing circuit envelope, the shim's drivers and
capability map do the routing, and results are `qhw-result-v1` records.

### Scope

- Pasqal Cloud devices (QPUs and emulators) through QRMI's `PasqalCloud`
  resource.
- A Pulser `RemoteConnection` for QFw, so a Pulser program runs on a QFw QPM
  the way it runs on Pasqal Cloud.

### Out of scope

- `PasqalLocal`, an on-premises QPU behind Pasqal's Warden. It opens with the
  Slurm job's identity and runs in a session acquired for that job, which a
  long-running shim service does not hold.

## 2. The program format

The circuit envelope gains one format:

| Format | Content |
| --- | --- |
| `pulser-abstract-repr` | A Pulser sequence in Pulser's JSON abstract representation, as `Sequence.to_abstract_repr()` writes it. |

It travels as `info["circuit"] = {"format": "pulser-abstract-repr", "data":
"<json>"}`, text like OpenQASM, so the scheduler keeps its bytes opaquely as
it does for any other format. `util.circuit_payload` reads it with
`pulser_sequence(info)` and writes it with `encode_pulser_sequence(sequence)`.

A Pulser sequence is a program for one provider family rather than a circuit
any QPM can transpile, so:

- a QPM declares it in `circuit_formats` only if it runs it, and a Pasqal QPM
  declares it alone;
- a QPM that reads circuits through Qiskit refuses it by name, and a Pasqal
  QPM refuses a circuit by name, before anything reaches a provider.

A sequence is sent fully built. Pulser allows variables, and a mappable
register, to be filled per job; the client builds the sequence with each job's
values first, because the provider takes one concrete sequence per job.

## 3. Finding a Pasqal QPM

A client looks a QPM up by type and capability bits, and a lookup's bits must
all be present in what the QPM advertises. The shim advertised the
superconducting bit for every device, so a Qiskit client asking for a
superconducting shim could land on a Pasqal one.

- `QPMCapability.QPM_CAP_NEUTRAL_ATOM` (bit 3) is new. The shim advertises it
  for a Pasqal device, and the superconducting bit for every other device, as
  before. The bit says what the device is, not what it reads: a client checks
  `circuit_formats` before it sends anything (Section 6).
- The client selection table gains `pasqal`: provider `shim`, hardware,
  neutral-atom. The Pulser client and the example use it; a reservation for a
  named device still picks the QPM serving that device.
- The directory record carries `technology: neutral-atom` and
  `device_provider: pasqal`.

## 4. Running through QRMI

A Pasqal device in device-access config:

```yaml
pasqal-fresnel:
  provider: pasqal
  provider-device-id: FRESNEL      # the QRMI resource id: EMU_FREE, FRESNEL, ...
  resource-type: PasqalCloud
  url: https://apis.pasqal.cloud
  project-id: PROJECT              # default project; a user's entry can name another
  libraries: [qrmi]
  credential-db: qpu-users.json
```

`resource-type` is required, because QRMI serves Pasqal through two resource
types.

**Authentication.** QRMI logs in and renews tokens; QFw picks whose identity
the resource uses, and never mixes two. The resource opens from a config map
(`QuantumResource.from_config`), which QRMI reads instead of the environment
and `$HOME`, so a qrmi without it is refused.

| Reservation credential | Runs as | Login in the map | Project |
| --- | --- | --- | --- |
| carries a token (file provider) | the user | the user's token only | the user's `project_id`, else the device's `project-id` |
| none, or no token (no-secret, entitlement) | the service | the service's own, below | the reservation's `project_id`, else the backend's, else the service user's credential DB entry, else the device's |

A user's resource is built from the credential and the device alone: its URL
is the credential's, else `QFW_QC_URL`, else the device's `url`, and nothing is
resolved for the service's own user. A service's URL is the backend's, else
device access's, else the device's `url`, so a failed lookup does not leave
QRMI on its default.

The service's own login is what the operator gives the QPM service: a token
(`{backend}_QRMI_PASQAL_CLOUD_AUTH_TOKEN` or `QFW_API_KEY`), a service account
(`{backend}_QRMI_PASQAL_CLOUD_CLIENT_ID` and `_CLIENT_SECRET`), or, opted into
explicitly, `PASQAL_CONFIG_ROOT` (or `{backend}_PASQAL_CONFIG_ROOT`) and
`PASQAL_USERNAME`/`PASQAL_PASSWORD`, which Pasqal's own tools read. Under a
config root the file may name the project too. `$HOME` is never passed.

A user's resource carries no service login, because QRMI renews an expired
token with whatever login it holds: the operator's service account beside a
user's token would carry on as the operator. A shared QPM serving users from
a credential DB therefore holds tokens it cannot renew, so they expire
(Section 8); one whose service account runs every user's jobs, billed to each
user's project, uses the entitlement provider.

A resource is cached per reservation, so a new reservation reads a refreshed
token or config file; one opened without a reservation lives as long as the
service.

**Execution.** `run_circuit` reads the sequence, submits it as
`Payload.PasqalCloud(sequence, job_runs)`, polls, and turns the batch's
counter into a `qhw-result-v1` record. The counts are keyed by atom in
register order, as Pulser's results are. Two details differ from the other
QRMI providers:

- A finished task is not deleted with `task_stop`. On Pasqal it cancels the
  batch, and Pasqal rejects cancelling one that has ended. A running task is
  still stopped on cancel or at its deadline.
- The provider deadline is the device's `job-timeout-seconds` (the key
  IBMQuantumSystem already hands QRMI), or a timeout the client sends, and
  reaching it cancels the job. Without one a job waits as long as the
  provider queues it: a cloud QPU's queue can outlast any default, and a
  reservation that closes cancels its jobs anyway. The other QRMI providers
  wait for `job-timeout-seconds` too, else 300 s. A value that is not a
  number of seconds fails the circuit before anything is submitted.
- A job is billed to a project, so it needs one (above); opening the resource
  does not.

**Capabilities.** A Pasqal resource serves `run_circuit`, the task timing and
metadata calls, and `get_backend_info` (Section 5). The qhw introspection calls
are a gap for it: QRMI's `target()` is a list of Pulser devices, which no qhw
normalizer reads.

## 5. Device specifications for clients

A Pulser program is built against a device: its channels, limits and the
calibrated register layouts a QPU accepts. Pulser checks a sequence against
the device's current specifications before it is submitted.

For a Pasqal device, `get_backend_info` returns those specifications, fetched
for each call so they follow recalibrations. An emulator's are public; a QPU's
need a login. A client passes its `reservation_id`, and the QPM reads the
device with that reservation's credential, resolved from its own bindings as
for execution. Without one the QPM reads it as itself.

```json
{
  "backend": "pasqal",
  "metadata_supported": true,
  "device_type": "EMU_FREE",
  "pulser_devices": {"FRESNEL": "<abstract repr>", "FRESNEL_CAN1": "<abstract repr>"}
}
```

`pulser_devices` is what Pasqal Cloud reports for the resource: the device
itself for a QPU, and for an emulator the public specifications of the real
devices, any of which the emulator runs. They are in Pulser's abstract
representation, which `pulser.abstract_repr.deserialize_device` reads.

## 6. The Pulser client

`backends/qfw_pulser.QFwConnection` is a Pulser `RemoteConnection`:

```python
backend = pulser.QPUBackend(sequence, connection=QFwConnection())
results = backend.run([{"runs": 100}], wait=True)
```

- It finds the QPM through the `pasqal` selection, or the reservation the
  launcher exported, and checks the QPM declares `pulser-abstract-repr`.
- `submit` checks the sequence against the device's current specifications
  and sends that sequence, each job as one circuit through `async_run`, built
  with its variables. (`QPUBackend.run` checks too, but hands `submit` the
  sequence it was given.) Every job is built before any is sent, so one job's
  bad variables fail the submit with nothing at the provider. A batch is the
  jobs of one submit and exists only in the connection.
- Once a job is accepted its batch stays reachable. A submit that fails part
  way cancels each accepted job on its own and raises `QFwBatchError` naming
  the batch, the submission failure as its cause and any cancel that failed,
  including one the QPM refused. An interrupt cancels them too and is raised
  as it is.
  A wait that times out or cannot poll raises it too and leaves the jobs
  running; `RemoteResults(batch_id, connection)` follows them, and
  `cancel_batch` stops them. `timeout` bounds the client's wait only.
- Job state comes from the QPM's completion queue (`read_cq`): a completion
  with `rc` 0 is done, a QPM-side or provider cancel is cancelled, anything
  else an error carrying the QPM's message, and a lost reservation an error.
  A job not yet complete is running once the QPM has submitted it to the
  provider, pending before.
- `fetch_available_devices` calls `get_backend_info` through the QPM's
  telemetry binding, as the reservation, and returns the deserialized
  `pulser_devices`, so
  `QPUBackend` checks a sequence against the device's current
  specifications. A QPM that serves none raises `NotImplementedError`, which
  Pulser treats as "keep the sequence's device".
- `lib` and `QFW_SHIM_LIB` pick the shim library, as for the Qiskit backend.

`setup/pulser-requirements.txt` holds the Pulser dependency, needed only by
applications that use this client.

## 7. Example

`examples/qfw_pulser.sh` starts the shim service for a Pasqal device, reserves
it and runs `examples/tests/test_pulser.py` through `QPUBackend`. The program
fetches the device through the QPM and places its atoms on a calibrated
layout, then drives them with a pi/2 and a pi pulse in two jobs. A device can
require a layout to be filled to a minimum fraction unless it has exactly the
minimum number of traps; the program uses such a layout when the device has
one, and otherwise raises the atom count (42 on FRESNEL's 120 traps).

Run on EMU_FREE with four atoms built on FRESNEL_CAN1's layout, 100 shots per
job, the pi/2 pulse leaves each atom excited half the time and the pi pulse
reads `1111` in almost every shot.

## 8. Open questions

- The envelope and the property are named for circuits (`info["circuit"]`,
  `circuit_formats`). A program-neutral name would read better once more than
  one non-circuit format exists.
- Pulser devices have no qhw device record, so the qhw introspection calls are
  a gap on Pasqal devices.
- A token in a credential DB is not renewed. QFw's credential providers have a
  `refresh` hook that nothing calls yet; until something does, a site serving
  users from a credential DB replaces tokens before they expire, or runs
  users' jobs as a service account with the entitlement provider.
- Only `get_backend_info` reads a device as a reservation. The other
  introspection calls take no reservation, as for every QPM.
- The client reuses the Qiskit frontend's lookup and job helpers
  (`qfw_qiskit`), so it needs Qiskit installed, as every QFw install has.
  Moving them to an SDK-neutral module would make the two clients siblings.
  The lookup also uses `service_id` only to pick among exported
  reservations.
- Discovery still matches the shim's hardware QPM type and a technology bit,
  for an emulator as for a QPU. A reservation for a named device is the
  precise way to pick one.
