#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "qiskit-simple" "$@"
qfw_example_setup

# Takes number of qubits
#  ex: test_qiskit_simple.py 10
qfw_example_srun "$(qfw_example_path tests/test_qiskit_simple.py)" "$1"
