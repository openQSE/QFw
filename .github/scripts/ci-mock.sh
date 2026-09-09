#!/usr/bin/env bash
# .github/scripts/ci-mock.sh — CI/local wrapper for the mock pytest suite.
#
# Called by CI:  .github/workflows/test-reusable.yml ("Run ci-mock" step)
# Run locally:   ./.github/scripts/ci-mock.sh
#
# Dependencies:  pip install pytest
set -e

echo "--- ci-mock tests ---"
PYTHONPYCACHEPREFIX=/tmp/qfw-pyc python -m pytest tests/mock -q

# The top-level suites run as their own pytest invocation, deliberately.
# tests/mock/conftest.py installs stub modules into sys.modules at import time,
# and pytest imports it while collecting tests/mock, so anything collected in
# the same run inherits those stubs. That is why `pytest tests` fails to
# collect tests/qiskit, which needs the real qiskit. Separate invocations keep
# the stubs contained to the suite that wants them.
#
# tests/qiskit is still excluded here: it needs a real qiskit install, which
# this job does not have.
echo "--- ci-mock unit tests ---"
PYTHONPYCACHEPREFIX=/tmp/qfw-pyc python -m pytest tests -q \
	--ignore=tests/mock --ignore=tests/qiskit

echo "ci-mock passed."
