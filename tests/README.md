# Tests Roadmap

## Purpose

This directory contains the CI-safe test plan and the current mock-based
test suite for QFw.

The immediate goal is to validate backend and module behavior on
GitHub-hosted runners without requiring the full distributed runtime
stack.

## Test Tiers

- `ci-fast`
  Lint, syntax, and other lightweight static checks.

- `ci-mock`
  Mock-based backend and module tests that do not require PRTE, DEFw
  daemons, QPM services, or simulator binaries.

- `integration-hpc`
  Real distributed-stack testing on self-hosted or cluster-backed
  runners.

This document tracks the `ci-mock` roadmap.

## Phase 1: Backend Mock Tests

Status:

- [x] Complete

Files:

- [conftest.py](/Users/3t4/projects/quantum/openQSE/scratch/scratch-QFw/tests/mock/conftest.py)
- [fakes.py](/Users/3t4/projects/quantum/openQSE/scratch/scratch-QFw/tests/mock/fakes.py)
- [test_qfw_lookup_service.py](/Users/3t4/projects/quantum/openQSE/scratch/scratch-QFw/tests/mock/test_qfw_lookup_service.py)
- [test_qfw_job.py](/Users/3t4/projects/quantum/openQSE/scratch/scratch-QFw/tests/mock/test_qfw_job.py)
- [test_qfw_backend_smoke.py](/Users/3t4/projects/quantum/openQSE/scratch/scratch-QFw/tests/mock/test_qfw_backend_smoke.py)

Checklist:

- [x] Add a `tests/mock` test area
- [x] Add repo-local import and stub setup for CI-safe test execution
- [x] Add fake QPM, event, runtime, and circuit helpers
- [x] Test QPM lookup success and failure behavior
- [x] Test job submission payload construction
- [x] Test job result translation from fake events into Qiskit-style results
- [x] Test backend initialization, event registration, run-path, and shutdown behavior
- [x] Confirm the suite passes under `pytest`

Phase 1 covers:

- `qfw_lookup_service` behavior around:
  - service reservation success
  - failed `test()` probe causing shutdown
  - reservation error propagation
- `QFwJob` behavior around:
  - request payload construction
  - single-circuit submission
  - fake event consumption
  - counts-to-memory mapping
  - propagation of submission failures
- `QFwBackend` behavior around:
  - QPM lookup hookup
  - event registration
  - job creation/submission
  - shutdown calling both QPM shutdown and runtime exit
- backend smoke coverage intentionally uses fake collaborators and a
  fake circuit input so the test remains stable across harmless import
  cleanup and internal refactors
- real circuit/payload behavior is covered in the `QFwJob` tests rather
  than in backend smoke tests

Phase 1 does not cover:

- PRTE startup
- DEFw daemon behavior
- real resource-manager or QPM services
- simulator execution
- `examples/tests`
- estimator or sampler behavior
- multi-node or HPC orchestration

Run Phase 1:

```bash
cd /Users/3t4/projects/quantum/openQSE/scratch/scratch-QFw
venv/bin/python -m pytest tests/mock -q
```

Current result:

- [x] `8 passed`

## Phase 2: Explicit Injection Seams

Status:

- [ ] Not started

Objective:

Reduce monkeypatching by making backend/runtime seams explicit in
production code.

Checklist:

- [ ] Allow `QFwBackend` to accept injected `qpm`
- [ ] Allow `QFwBackend` to accept injected `event_api`
- [ ] Allow `QFwBackend` to accept injected `qpm_resolver`
- [ ] Allow `QFwBackend` to accept injected `runtime_api`
- [ ] Preserve backward-compatible default behavior
- [ ] Update Phase 1 tests to prefer direct injection over global patching

Phase 2 should cover:

- explicit backend construction with fake collaborators
- cleaner and less fragile unit tests
- clearer separation between backend logic and runtime wiring

Phase 2 still will not cover:

- full distributed integration
- real PRTE/DEFw/QPM startup
- simulator binary execution

Run Phase 2:

- Not available yet

## Phase 3: Sampler and Estimator Mock Tests

Status:

- [ ] Not started

Objective:

Add CI-safe coverage for the higher-level primitive wrappers.

Checklist:

- [ ] Add `tests/mock/test_qfw_sampler.py`
- [ ] Add `tests/mock/test_qfw_estimator.py`
- [ ] Test shot grouping behavior
- [ ] Test precision grouping behavior
- [ ] Test warning and validation paths
- [ ] Test result container shape and metadata

Phase 3 should cover:

- sampler orchestration logic
- estimator orchestration logic
- batching/grouping behavior above the backend layer

Phase 3 still will not cover:

- real runtime/service integration
- simulator execution fidelity

Run Phase 3:

- Not available yet

## Phase 4: Optional Runtime Adapter Isolation

Status:

- [ ] Not started

Objective:

Move runtime-specific integration into a narrow adapter layer and keep
the backend logic more directly testable.

Checklist:

- [ ] Identify DEFw/QPM-specific wiring that can move into an adapter
- [ ] Narrow direct runtime access inside backend modules
- [ ] Keep unit-testable logic separate from integration-only paths

Phase 4 should improve:

- architecture clarity
- long-term maintainability
- confidence that unit tests and integration tests are testing distinct concerns

Run Phase 4:

- Not available yet

## CI Wiring

For the current Phase 1 `ci-mock` path, CI only needs:

- Python 3.11 or 3.12
- `pytest`
- a test command:

```bash
python -m pytest tests/mock -q
```

Notes:

- Phase 1 uses [conftest.py](/Users/3t4/projects/quantum/openQSE/scratch/scratch-QFw/tests/mock/conftest.py) to stub runtime-only dependencies.
- Phase 1 does not require installing `qiskit`, `numpy`, or `qiskit_aer` in CI.
- Real runtime validation remains a separate `integration-hpc` concern.
