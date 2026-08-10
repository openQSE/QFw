#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "pennylane" "$@"
qfw_example_setup_backend_service nwqsim

# Tests pennylane with nwqsim
qfw_example_srun_with_backend_reservation \
	nwqsim pennylane 2 1024 1 async_run \
	"$(qfw_example_path tests/test_pennylane.py)"
