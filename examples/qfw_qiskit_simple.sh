#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "qiskit-simple" "$@"
qfw_example_setup_backend_service nwqsim

# Takes number of qubits
#  ex: test_qiskit_simple.py 10
qfw_example_srun_with_backend_reservation \
	nwqsim qiskit-simple "$1" 1024 1 async_run \
	"$(qfw_example_path tests/test_qiskit_simple.py)" "$1"
