# Phase 19 QFwBackendV2 Gate

This gate qualifies the Qiskit 2.2.3 adapter against installed libquantum,
QFw helper, and Qiskit compiler wheels. It needs no network, reservation
service, credential, or quantum device.

Frozen revisions for the retained run are libquantum `5b066a225e39`, QFw
`3de71470c0bb`, and libquantum-compiler-qiskit `698062f20752`. Package versions
are `libquantum==0.1.0.dev0`, `qfw-qiskit==0.0.1`,
`libquantum-compiler-qiskit==0.1.0`, and `qiskit==2.2.3`.

## Installed-wheel gate

Use a Python 3.12 environment containing Qiskit 2.2.3 and pytest. Build the
three distributions and install them into an empty target directory.

```bash
WHEELS=$(mktemp -d /tmp/qfw-v2-wheel-XXXXXX)
TARGET=$(mktemp -d /tmp/qfw-v2-install-XXXXXX)
python3.12 -m pip wheel --no-deps -w "$WHEELS" \
  /path/to/libquantum \
  /path/to/QFw/backends \
  /path/to/libquantum-compiler-qiskit
python3.12 -m pip install --no-deps --target "$TARGET" \
  "$WHEELS"/*.whl
```

Run the focused tests from `/tmp` so source packages cannot shadow installed
wheels.

```bash
cd /tmp
LIBQUANTUM_SOURCE=/path/to/libquantum \
PYTHONPATH="$TARGET" python3.12 -m pytest -q \
  /path/to/QFw/tests/qiskit/test_qfw_backend_v2.py \
  /path/to/QFw/tests/qiskit/test_qfw_job_v2.py \
  /path/to/QFw/tests/qiskit/test_qfw_job_v2_results.py \
  /path/to/QFw/tests/phase_tests/test_phase_19_qfw_backend_v2.py
```

Expected result is `23 passed`. The phase test exercises direct backend
execution, `BackendSamplerV2`, and `BackendEstimatorV2` through the normal
compiler manifest, qhw target helper, libquantum route, completion queue, and
QRI/QPM boundary.

The retained dummy query names service `qpm-a`, reservation `101`, device
`dummy:compiler`, two usable qubits, execution binding version 1, and one
accepted `openqasm` version `2.0` input. Adapter policy chooses the sole
`qiskit` compiler route. The trace contains one operation for direct execution
and the operations created by sampler and estimator execution. Every
submission carries the compiler-owned OpenQASM envelope by identity plus its
runtime shot count. Completion events are correlated by `cid`; focused tests
deliver them out of order and verify restored circuit order. Each job closes
its queue after consuming terminal completions, and backend shutdown finalizes
the context with no outstanding operation identifiers.

The focused tests also cover complete-batch compilation before submission,
partial immediate submission failure, out-of-order completions, status
aggregation, confirmed cancellation, result timeout and retry, result caching,
payload validation, and shutdown timeout reporting.

## Legacy coexistence

Run the unchanged legacy backend and job tests from the QFw repository root.

```bash
PYTHONPATH=tests/mock:backends:services:service-apis:setup \
  python3 -m pytest -q \
  tests/mock/test_qfw_backend_smoke.py \
  tests/mock/test_qfw_job.py
```

Expected result is `16 passed, 1 skipped`. The skip is a pre-existing
phase-specific legacy result-memory test and is not V2 qualification. The V2
and legacy exports resolve to separate modules and no fallback path exists.
