# Run against Pasqal through QDMI

Pasqal accepts native Pulser sequences. It does not compile Qiskit gate
circuits into pulses. Use Python 3.11 or newer and install the released
packages from `setup/qdmi-requirements.txt`, which pins
[`pasqal-qdmi[core,pulser]==1.0.0`](https://pypi.org/project/pasqal-qdmi/1.0.0/).

The provider's `pulser` extra installs Pulser for sequence serialization.
The existing `qfw_pulser.sh --libs qdmi` example
uses the same frontend and request format as the QRMI path.

## 1. Configure credentials

A user's existing credential record can bind `api_key`/`token` and
`project_id` to the reservation, as for QRMI. The project defaults to the
device's `project-id`. The example also accepts `QFW_API_KEY` and the project
it configures from `QFW_PASQAL_PROJECT_ID`.

For native service-account authentication, supply `PASQAL_EMAIL`, `PASQAL_PASSWORD`, and the full project UUID in
`PASQAL_PROJECT_ID` through the site's secret-management mechanism. Alternatively,
set `PASQAL_AUTHFILE` to a private UTF-8 file containing `username`, `password`,
and `project_id` as `key=value` lines. Restrict it to its owner (`chmod 600`).
See the provider's [authentication guide](https://pasqal-qdmi-device.readthedocs.io/en/stable/authentication.html).
A bound token overrides `QFW_API_KEY`, and a bound URL overrides
`QFW_QC_URL` and the device URL. Token sessions disable native login-file
fallback. Unset `PASQAL_EMAIL` and `PASQAL_PASSWORD` in token mode so an
expired token cannot refresh as the service account, as the IQM profile
also rejects conflicting process-wide session settings.

With service-account authentication, authorized users share that account. Use
QFw's existing `entitlement` credential provider for reservation access control;
keep provider secrets out of device-access YAML and entitlement records.

## 2. Add the device

In the site's device-access file:

```yaml
credential-providers:
  pasqal-access:
    type: entitlement
    credential-db: /etc/openqse/qfw/device/qpu-users.json

qpus:
  pasqal-emu-free:
    provider: pasqal
    provider-device-id: EMU_FREE
    qdmi-device-id: pasqal.emu-free
    url: https://apis.pasqal.cloud
    credential-provider: pasqal-access
    libraries: [qdmi]
    preference: qdmi
    execution-owner: qdmi
    job-timeout-seconds: 3600
    max-shots: 100
    caps:
      get_backend_info: [qdmi]
      get_device_info: [qdmi]
      run_circuit: [qdmi]
      get_task_timing: [qdmi]
      get_task_metadata: [qdmi]
```

Enable each user under `users.<user>.devices.pasqal-emu-free` with
`{"enabled": true}` in the credential database. Add an ordinary `svc_lib_qpm`
service for `device-id: pasqal-emu-free`, using `credential-mode: required`,
as described in the [machine support guide](../qfw-shim-provider-guide.md#step-4-add-a-qpm-shim-service).
For Slurm, map the public QPU name to that service using the same site mapping
as the existing QDMI devices.

`provider-device-id` is the backend type, for example `EMU_FREE`, `EMU_MPS`,
or `FRESNEL`. `qdmi-device-id` selects the installed catalogue entry and
defaults to `pasqal.default`; choose an entry and URL appropriate to the
backend and region. EMU_FREE is the free emulator; other backends can consume
credits. Choose the timeout and shot cap deliberately: QFw attempts cancellation
when the timeout expires, and cancellation support depends on the backend.

`get_backend_info` serves the cloud's current Pulser specs through the
same `pulser_devices` mapping as QRMI. The device record reports atom
capacity with an empty physical-site list.
Atom placement belongs to each sequence, so the configuration does not advertise
gate coupling or gate calibration calls. Set `num-qubits` if the directory
record needs a site-configured capacity before opening a session.

## 3. Submit native Pulser JSON

Build a bound sequence and pass it in the existing circuit envelope. This
two-atom example excites the first atom in register insertion order:

```python
import math
from pulser import Pulse, Register, Sequence
from pulser.devices import MockDevice

sequence = Sequence(Register({"left": (-10, 0), "right": (10, 0)}), MockDevice)
sequence.declare_channel("local", "rydberg_local", initial_target="left")
sequence.add(Pulse.ConstantPulse(1000, math.pi, 0, 0), "local")
sequence.measure("ground-rydberg")

info = {
    "circuit": {"format": "pulser-abstract-repr", "data": sequence.to_abstract_repr()},
    "num_qubits": 2,
    "num_shots": 10,
    "lib": "qdmi",
}
# execution is the existing execution-category QPM API for this device;
# reservation_id is the authorized reservation, as for every shim device.
response = execution.async_run(info, reservation_id)
```

Use a device specification suitable for the chosen backend; `MockDevice` is
appropriate for this ideal EMU_FREE example. Keep the returned circuit ID and
consume the existing circuit-result event or completion queue. The result uses
the same `counts` and `qhw_result` envelope as other shim devices, with provider
`pasqal` and the Pasqal batch ID in task metadata.

The native library puts atom index zero on the right for Core. QFw reverses
those keys to match the existing Pulser frontend and QRMI path, where
register insertion order reads left to right. The example therefore
produces predominantly `"10"`. Atom indices follow register insertion
order, independent of label sorting or coordinates. One QFw request submits one
sequence; prepare variable bindings and mappable registers in Pulser first.

## 4. Validate without a cloud submission

```bash
python -m pytest -q tests/mock/test_pasqal_profile.py
python -m pytest -q tests/qiskit/test_pasqal_qdmi.py
```

The first suite checks policy and shared-driver submission with stand-ins. The
second follows the released-provider test naming and location used by IBM and
Braket; it validates real Pulser serialization and exercises the PyPI native
library against a local HTTP fixture. Neither submits a cloud job. The existing
shim smoke can query `get_device_info` for this device; its gate-circuit execution
fixtures are for the gate-based providers.
