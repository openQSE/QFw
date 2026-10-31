#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_execution_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

qfw_example_begin "qiskit-simple-w-mqt" "$@"
backend="$(qfw_example_backend nwqsim)"
qfw_example_setup_backend_service "${backend}"

# Takes number of qubits followed by optional Python example arguments.
#  ex: qfw_qiskit_simple_w_mqt.sh 10 --backend-api v2
qfw_example_srun_with_backend_reservation \
	"${backend}" qiskit-simple-w-mqt "$1" 1024 1 async_run \
	"$(qfw_example_path tests/test_qiskit_simple_w_mqt.py)" \
	"$1" "${backend}" "${@:2}"
