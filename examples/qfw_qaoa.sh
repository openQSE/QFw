#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "qaoa" "$@"
qfw_example_setup_backend_service "$1"

# takes the simulator type: nwqsim or tnqvm
qfw_example_srun "$(qfw_example_path tests/test_qiskit_qaoa.py)" "$1"
