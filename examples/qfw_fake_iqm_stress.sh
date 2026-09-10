#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

usage() {
	cat <<EOF
Usage: ./qfw_fake_iqm_stress.sh [--verbose] [driver options]

Start a local DEFw directory service and deterministic fake IQM QPM, then run
the qhw-admission/qhw-scheduler stress driver through qfw-srun.

Common driver options:
  --scenario-set NAME      startup, smoke, admission, workload, hybrid,
                           scheduler, or all
  --workers N              Concurrent application workers per scenario
  --tasks-per-worker N     Circuits per worker reservation
  --waves N                Application waves per heavier scenario
  --harness-walltime SEC   Per-scenario walltime bound
  -h, --help               Show driver help
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help-wrapper" ]]; then
	usage
	exit 0
fi

qfw_example_begin "fake-iqm-stress" "$@"
qfw_example_setup_local_services qfw_fake_iqm_services.yaml fake-iqm

qfw_fake_iqm_app_nodes="${QFW_FAKE_IQM_APP_NODES:-1}"
qfw_fake_iqm_app_tasks="${QFW_FAKE_IQM_APP_TASKS:-1}"
qfw_example_srun \
	--nodes "${qfw_fake_iqm_app_nodes}" \
	--ntasks "${qfw_fake_iqm_app_tasks}" \
	"$(qfw_example_path tests/test_fake_iqm_stress.py)" \
	"$@"

qfw_example_teardown
qfw_example_finish 0
