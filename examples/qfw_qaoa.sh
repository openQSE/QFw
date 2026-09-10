#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_execution_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

backend="$(qfw_example_backend "${1:-nwqsim}")"

qfw_example_begin "qaoa" "$@"
qfw_example_setup_backend_service "${backend}"

# takes the simulator type: nwqsim or tnqvm
qfw_example_srun_with_backend_reservation \
	"${backend}" qaoa 4 1024 "${QFW_QAOA_RESERVATION_TASKS:-32}" async_run \
	"$(qfw_example_path tests/test_qiskit_qaoa.py)" "${backend}"
