#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_execution_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

qfw_example_begin "statevector-correctness" "$@"
backend="$(qfw_example_backend nwqsim)"
qfw_example_setup_backend_service "${backend}"

# Takes number of qubits
#  ex: qfw_statevector_correctness.sh 4
qfw_example_srun_with_backend_reservation \
	"${backend}" statevector-correctness "$1" 1024 1 async_run \
	"$(qfw_example_path tests/test_statevector_correctness.py)" "$1" "${backend}"
