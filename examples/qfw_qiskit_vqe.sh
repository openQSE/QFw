#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "qiskit-vqe" "$@"
qfw_example_setup

# takes the number of VQE iterations
qfw_example_srun "$QFW_PATH/examples/tests/test_qiskit_vqe.py" "$1"
