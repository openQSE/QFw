# Measurement results

Raw output from the scripts in `examples/`, kept so the numbers quoted in the
QRMI/QDMI analysis can be traced to the run that produced them.

Results are environment-specific. Read the conditions before citing any figure.

## `2026-08-04-introspection-cold-warm.json`

Produced by `examples/measure_shim_introspection.py --repeat 5
--warm-iterations 10`.

**Conditions**

| | |
|---|---|
| Device | ORNL IQM 20-qubit (`ornl-iqm-20q`, provider alias `default`) |
| Host | container node `c5` |
| Network | **over an SSH tunnel from outside the ORNL network** |
| Cold samples | 5, each in a fresh subprocess |
| Warm iterations | 10 per call, per sample |
| Payload validated | 20 qubits returned on every sample |

**Versions.** These figures are implementation-specific and will change as the
libraries move, so they mean nothing without the versions they were taken
against: `qrmi` 0.17.2, `iqm-qdmi` 1.2.0, `mqt-core` 3.7.0 (QDMI headers and
FoMaC), `iqm-client` 34.0.1, `iqm-station-control-client` 12.1.1, `iqm-pulse`
13.0.1, `qhw-iqm` 0.1.0.

**Medians**

| | QRMI | QDMI |
|---|---|---|
| open | 3.1 ms | 3351.7 ms |
| first call | 1331.5 ms | 25.1 ms |
| **cold total** | **1334.6 ms** | **3376.8 ms** |
| warm `get_device_info` | 11.4 ms | 12.0 ms |
| warm `get_coupling_graph` | 16.4 ms | 16.1 ms |
| warm `get_calibration_snapshot` | 18.1 ms | 12.4 ms |

Cold spread: QRMI 1238-1500 ms, QDMI 3193-3397 ms.

**What the numbers show**

The two libraries pay their network cost in opposite phases — QRMI at the first
call (`target()`, then cached per driver instance), QDMI at open (session init
fetches the device data). Cold total is the comparable figure.

Warm cost is indistinguishable between them and involves no network on either
path, so it is dominated by `qhw` normalization rather than by the interface.
Neither library has a warm-path advantage.

**Why QDMI's cold cost is higher**

Not only request count. A companion check counted distinct TCP connections
opened toward the endpoint during one cold introspection:

| | requests | connections | TLS handshakes |
|---|---|---|---|
| QRMI | 3 | **1** | 1 |
| QDMI | ~5 | **5** | 5 |

QRMI's Rust client (`reqwest`) pools and reuses one connection. QDMI-on-IQM
1.2.0 issues each request through cpr's free-function API (`cpr::Get` /
`cpr::Post` in `src/internal/http_client.cpp`), which constructs and destroys a
session — and with it the underlying libcurl handle and its connection cache —
per call. No session is retained across requests, so each one reconnects and
repeats the TLS handshake.

Solving the two medians for a uniform per-request cost and a uniform handshake
cost gives roughly 330 ms per request and 350 ms per handshake under these
conditions — a handshake costing about as much as a request, which is what one
would expect over a tunnel. That is a derived model from two data points, not a
measurement; it is offered only as a consistency check on the explanation.

This is an implementation property of QDMI-on-IQM, not a property of the QDMI
interface, and it is fixable there by retaining a session across requests.

**Caveats**

- Measured **over an SSH tunnel**, so every round trip carries an extra hop.
  This amplifies any difference in connection count, and the on-site ratio
  should be smaller. An on-site re-run is the most valuable follow-up.
- The payloads are not equivalent. QDMI's session init also fetches the static
  quantum architecture, which QRMI's `target()` does not expose at all. QDMI's
  higher cold cost buys strictly more device data.
- One device, one session, five samples.

## `2026-08-04-execution-phases.json`

Produced by `examples/measure_shim_execution.py --repeat 2
--count-connections`. Same device, same path, same versions as above. **Uses
QPU time**: two circuits per library, one qubit, 10 shots.

Medians per run:

| | QRMI | QDMI |
|---|---|---|
| prep (transcode + payload assembly) | 67.7 ms | 66.2 ms |
| submit | 133.1 ms | 1590.6 ms |
| wait (poll to terminal) | 589.9 ms | 1339.5 ms |
| fetch | 300.1 ms | 522.3 ms |
| **total** | **1090.8 ms** | **3518.5 ms** |
| connections opened | 1 | 3 |

Both returned `{'1': 10}` — an X gate on |0⟩, no readout error at 10 shots.

**Payload assembly costs nothing.** This measurement was built to test whether
the envelope-assembly difference between the libraries is expensive: QRMI's
caller builds the whole IQM run request, QDMI's caller submits a single circuit
and the device implementation assembles the request. The two are within 1.5 ms
of each other. The difference is architecturally significant — it decides who
owns shots, calibration selection, and provenance — but it is not a performance
difference.

**The gap is connection handling again, now multiplied by polling.** The same
per-request reconnect seen in introspection applies here, and execution makes
several requests: submit, one or more polls, then fetch. QRMI does all of them
over one pooled connection; QDMI opens a new one each time.

**Read `wait` with care.** It includes device queue time, which neither
interface reports separately (see the Telemetry axis of the QRMI/QDMI analysis),
so it varies with what else is on the instrument. `submit` and `fetch` are the
interpretable phases; `wait` is confounded.

**Ordering artifact, since fixed.** The first version charged the first library
measured about 3.4 seconds of lazy Qiskit imports inside `build_iqm_circuit`,
making its payload assembly look ~57x more expensive than the second. Confirmed
as positional by reversing the library order and watching the penalty follow
position. The script now warms the transcoder before timing; prep is
order-independent.

## Three-arm records: `2026-08-04-introspection-three-arm.json`, `2026-08-04-execution-three-arm.json`

The same two measurements re-run with QFw's **native IQM service client**
(`svc_iqm_qpm`) added as a third path, talking to `iqm-client` directly. This is
the baseline the interface-convergence question needs: it separates what the
shim layers cost from what they save.

Introspection, median of 3 cold samples:

| | cold | cold conns | warm `get_device_info` | new conns during warm |
|---|---|---|---|---|
| QRMI | 918.1 ms | 1 | 11.8 ms | 0 |
| QDMI | 2881.0 ms | 5 | 13.1 ms | 0 |
| native | 2831.1 ms | 4 | **512.6 ms** | **25** |

Execution, one circuit each:

| | total | conns | provider timing |
|---|---|---|---|
| QRMI | 995.7 ms | 1 | none |
| QDMI | 3892.7 ms | 4 | none |
| native | 3992.7 ms | 6 | queue 34.9 ms, execution 107.3 ms |

**The interface layers are not pure overhead — they add a cache the native
client does not have.** Both QRMI and QDMI serve repeat introspection locally,
opening no connection. The native path holds no introspection cache at all:
every call re-fetches, 25 connections across a warm phase, and each call costs
about 40x what the cached paths cost. It also re-fetches the dynamic
architecture inside `run_circuit`, which is why its execution `prep` is high.

**Only the native path reports provider-side timing.** It reads the IQM job
timeline and can separate queue wait from execution — 34.9 ms and 107.3 ms in
the run above. Neither QRMI nor QDMI passes that through. The information is
not missing at the provider; both interfaces discard it. That materially
changes the reading of the Telemetry gap in the analysis document: it is a
choice made by the abstraction layers, not a limitation of the device.

Worth noting how small the device's own numbers are. Execution took 107 ms on
hardware while the client-side total was 1-4 seconds. Under these conditions
almost everything measured is client and network cost, not QPU time.

**Reproducing**

```bash
python examples/measure_shim_introspection.py --repeat 5 --warm-iterations 10
```

The connection counts in this record were taken by hand with `ss`. Counting is
now built into the script behind `--count-connections`, which reproduces the
same figures (QRMI 1, QDMI 5) and additionally confirms that the warm phase
opens no connection on either path:

```bash
python examples/measure_shim_introspection.py --repeat 5 --warm-iterations 10 --count-connections
```

Execution (**uses QPU time** — keep `--repeat` and `--shots` small):

```bash
python examples/measure_shim_execution.py --repeat 2 --count-connections
```
