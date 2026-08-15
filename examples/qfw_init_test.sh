#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "init-test" "$@"
qfw_example_setup_qpm_services nwqsim tnqvm

qfw_example_srun --nodes 1 --ntasks 1 \
	"$(qfw_example_path tests/test_init_qfw.py)" nwqsim-statevector
qfw_example_srun --nodes 1 --ntasks 1 \
	"$(qfw_example_path tests/test_init_qfw.py)" tnqvm-tensor
qfw_example_srun --nodes 1 --ntasks 1 \
	"$(qfw_example_path tests/test_init_qfw.py)" tnqvm-default
qfw_example_srun --nodes 1 --ntasks 1 \
	"$(qfw_example_path tests/test_init_qfw.py)" qb-missing
