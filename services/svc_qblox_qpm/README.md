# svc_qblox_qpm

A QPM service for the Qblox control stack, built on `qblox-scheduler`'s
`HardwareAgent`. It is shaped exactly like `svc_iqm_qpm`:

| File | IQM analogue | Contents |
|---|---|---|
| `__init__.py` | `svc_iqm_qpm/__init__.py` | `svc_info`, `initialize`/`uninitialize` |
| `svc_qpm.py` | `svc_iqm_qpm/svc_qpm.py` | `QPM(UTIL_QPM)`, device profile, `query()` |
| `svc_qrc.py` | `svc_iqm_qpm/svc_qrc.py` | completion queue, sync/async dispatch |
| `util_qblox.py` | `util_iqm.py` | `QbloxServiceClient` — the control stack |
| `configs/` | — | `hw_cfg.json`, `dev_cfg.json` |

`svc_qrc.py` is provider-agnostic plumbing and is near-identical to IQM's.
Everything that knows what Qblox is lives in `util_qblox.py`.

## Installing

The Qblox stack is **not** in `setup/requirements.txt`. It is optional, the
way Pulser and the QDMI adapters are, because `qblox-scheduler` pulls in
PyQt5 and pyqtgraph and its import needs a system OpenGL library:

```bash
pip install -r setup/qblox-requirements.txt -c setup/requirements.txt
```

The import is not headless — `qblox_scheduler/__init__` reaches
`quantify_core.visualization` → `pyqtgraph` → `PyQt5.QtGui`, which dlopens
`libGL.so.1`. Install the system package too: `mesa-libGL` on Rocky,
`libgl1` on Debian. Without the stack the service reports a clear error
through `check_qblox_packages()`; the rest of QFw is unaffected.

## Two service IDs, one module

Which identity the process takes is decided **at startup**, from whichever
configuration source actually resolves a cluster address — the environment,
a `.env`, or `device-access.yaml`. `resolve_service_identity()` does this,
and the answer sets both the advertised `QPMType` and the target ID:

| Service | Address source | QPM type | Lifecycle |
|---|---|---|---|
| `qblox-dummy` | none | simulator | allocation-local, like `fake-iqm` |
| `qblox-cluster` | `device-access.yaml` | hardware | `operation-mode: long-running` |

Reading `QBLOX_CLUSTER_IP` alone is not enough: a site deployment gives the
cluster service its address only through `device-access.yaml`, so a service
driving real hardware would advertise itself as the simulator, the
`qblox-cluster` backend key would match no QPM, and `qblox-dummy` could be
dispatched to the instrument.

The dummy is `qblox-scheduler`'s built-in software cluster: pure software,
no device access, no route to an instrument.

## Where the cluster address comes from

Service manifests have no `environment:` block, so the real cluster's
address reaches the service through the operator-owned device-access file.
QFw sets `QFW_QPU_DEVICE_ID` from the manifest's `device-id` and points
`QFW_DEVICE_ACCESS_CFG` at the file; the client then resolves it:

```yaml
qpus:
  qblox-cluster:
    provider: qblox
    provider-device-id: cluster0
    url: 192.0.2.10
```

There is no `credential-db`: a Qblox cluster is an address on a lab network,
not a web API with a token. That is why `credential-mode` is `no-secret`.

Resolution order, highest first:

1. a per-reservation credential carrying `cluster_ip`
2. `QBLOX_CLUSTER_IP` in the environment
3. `device-access.yaml`, when `device-id` and `QFW_DEVICE_ACCESS_CFG` are set
4. nothing — the dummy cluster

## Configuration

| Variable | Meaning |
|---|---|
| `QBLOX_CLUSTER_IP` | Cluster address. Unset selects the dummy cluster. |
| `QBLOX_CLUSTER_NAME` | Cluster name, default `cluster0`. |
| `QBLOX_HARDWARE_CONFIG` | Absolute path to `hw_cfg.json`. |
| `QBLOX_DEVICE_CONFIG` | Absolute path to `dev_cfg.json`. |
| `QBLOX_ENV_FILE` | Path to a `.env`; defaults to `.env` beside this file. |
| `QBLOX_PROFILE_STAGES` | `1` to record per-stage timings. |
| `QFW_QBLOX_MAX_QUBITS` | Override the count read from `dev_cfg.json`. |
| `QFW_QBLOX_MAX_SHOTS` | Shot ceiling, default 10000. |
| `QFW_QBLOX_TARGET_ID` | Override the service target id. |
| `QFW_QBLOX_JOB_TIMEOUT` | Seconds a single circuit may run, default 300. `0` disables it. |
| `QFW_QBLOX_WAIT_DONE_TIMEOUT` | Seconds to wait for acquisition, default 10. |

Relative config paths resolve against `configs/`, never the working
directory: a QFw service starts in a generated run directory, on a different
node than the one that launched it.

A `.env` beside this file is read at import (see `.env.example`); short names
like `cluster_ip` map to their `QBLOX_*` equivalents, and `None` means unset.
The process environment wins over the file, so the manifest and
`device-access.yaml` always take precedence.

## Concurrency

`HardwareAgent` and the instrument coordinator's `stop()`/`prepare()`/
`start()` are global device operations — two circuits executing at once would
corrupt each other's acquisition. Enforced in three places:

- `max_ppn=1` on `UTIL_QPM`
- `concurrent_jobs: 1` in the device profile, so qhw-scheduler never
  dispatches two
- an explicit lock around the execute path in `QbloxServiceClient`

`async_run` means "the caller does not block", not "two circuits run at once".

## Known gaps

- **Device-profile timings are placeholders.** `QBLOX_PROFILE_STAGES=1`
  records real per-stage timings through `get_task_timing()`; sweep qubit
  count and depth and derive the coefficients from that.
- **The shipped `dev_cfg.json` declares 2 qubits**, with `acq_rotation` and
  `acq_threshold` both 0 (uncalibrated). Circuits wider than 2 qubits are
  rejected by admission, which is correct.
- **No cancellation.** Qblox has no mid-schedule abort, so `cancel()` can
  only stop a queued task from starting.
- **`QFW_QBLOX_JOB_TIMEOUT` bounds the caller, not the instrument.**
  `agent.run()` takes no timeout, so it runs on a worker that is waited for
  with one. On expiry the instrument coordinator is stopped and the agent is
  dropped, which frees the execute lock and makes the next circuit rebuild
  from scratch — but CPython cannot kill the worker, so an orphaned run may
  still be winding down. Dropping the agent is what stops it being the one
  that answers next.
- **Dummy readout is degenerate.** `set_dummy_binned_acquisition_data` is not
  wired up, so the dummy is a plumbing test, not a believable GHZ
  distribution.
