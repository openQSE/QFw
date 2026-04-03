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

echo "ci-mock passed."
