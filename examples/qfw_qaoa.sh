#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "qaoa" "$@"
qfw_example_setup_backend_service "$1"

# takes the simulator type: nwqsim or tnqvm
qfw_example_srun_with_backend_reservation \
	"$1" qaoa 4 1024 "${QFW_QAOA_RESERVATION_TASKS:-32}" async_run \
	"$(qfw_example_path tests/test_qiskit_qaoa.py)" "$1"
