#!/bin/bash

set -euo pipefail
set -x

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

qfw_example_begin "supermarq" "$@"
qfw_example_setup

# test_supermarq.py takes
#   run: sync or async
#   iterations: number of iterations to run the test
#   startquibt: The number of qubits to start with. Increases by one if
#               increas is true
#   shots: number of shots
#   increase: increase the number of qubits per iteration
#   method: ghz or vqe
#   backend: The backend type to use: tnqvm, nwqsim or qb
#
qfw_example_srun "$(qfw_example_path tests/test_supermarq.py)" --run "$1" \
			--iterations "$2" --startqbit "$3" --shots "$4" \
			--increase "$5" --method "$6" --backend "$7"
