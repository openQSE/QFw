#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

qfw_example_begin "qiskit-vqe" "$@"
qfw_example_setup_backend_service nwqsim

# takes the number of VQE iterations
max_iter="${1:-50}"
qfw_example_srun_with_backend_reservation \
	nwqsim qiskit-vqe 4 1024 "${max_iter}" async_run \
	"$(qfw_example_path tests/test_qiskit_vqe.py)" "${max_iter}"
