#!/usr/bin/env bash
# scripts/ci-checks.sh — single source of truth for all CI checks.
#
# Called by CI:  .github/workflows/test-reusable.yml ("Run CI checks" step)
# Run locally:   ./scripts/ci-checks.sh
#
# Dependencies:  pip install flake8
#
# To add, remove, or change a check, edit this file only.
# Also update the Dependencies line above if new tools are required.
set -e

FLAKE8_DIRS="services/ service-apis/ backends/ examples/"

echo "--- flake8 lint ---"
#flake8 --config DEFw/.flake8 $FLAKE8_DIRS
flake8 --config scripts/.flake8 $FLAKE8_DIRS

echo "--- syntax check ---"
find $FLAKE8_DIRS -name "*.py" -print0 | xargs -0 python -m py_compile

echo "All checks passed."
