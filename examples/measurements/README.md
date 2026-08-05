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
calls `curl_easy_init()` per request and `curl_easy_cleanup()` after it
(`src/internal/curl_http_client.cpp`); libcurl's connection cache lives on the
easy handle, so each request opens a fresh connection and repeats the TLS
handshake.

Solving the two medians for a uniform per-request cost and a uniform handshake
cost gives roughly 330 ms per request and 350 ms per handshake under these
conditions — a handshake costing about as much as a request, which is what one
would expect over a tunnel. That is a derived model from two data points, not a
measurement; it is offered only as a consistency check on the explanation.

This is an implementation property of QDMI-on-IQM, not a property of the QDMI
interface, and it is fixable there (a shared handle, or reusing the easy
handle across requests).

**Caveats**

- Measured **over an SSH tunnel**, so every round trip carries an extra hop.
  This amplifies any difference in connection count, and the on-site ratio
  should be smaller. An on-site re-run is the most valuable follow-up.
- The payloads are not equivalent. QDMI's session init also fetches the static
  quantum architecture, which QRMI's `target()` does not expose at all. QDMI's
  higher cold cost buys strictly more device data.
- One device, one session, five samples.

**Reproducing**

```bash
python examples/measure_shim_introspection.py --repeat 5 --warm-iterations 10
```

Connection counting is not part of the script. It was done by sampling
`ss -tan` for sockets to the endpoint port while a single-library run executed,
and counting distinct local ports.
