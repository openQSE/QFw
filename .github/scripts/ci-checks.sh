#!/usr/bin/env bash
# .github/scripts/ci-checks.sh — CI/local wrapper for lint and syntax checks.
#
# Called by CI:  .github/workflows/test-reusable.yml ("Run ci-checks" step)
# Run locally:   ./.github/scripts/ci-checks.sh
#
# Dependencies:  pip install flake8
set -e

FLAKE8_DIRS="services/ service-apis/ backends/ examples/"

echo "--- flake8 lint ---"
flake8 --config .github/scripts/.flake8 $FLAKE8_DIRS
#flake8 --config DEFw/.flake8 $FLAKE8_DIRS

echo "--- syntax check ---"
find $FLAKE8_DIRS -name "*.py" -print0 | xargs -0 python -m py_compile

echo "ci-checks passed."
