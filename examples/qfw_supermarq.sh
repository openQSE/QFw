#!/bin/bash

set -xe

qfw_setup.sh

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
qfw_srun.sh "$QFW_PATH/examples/tests/test_supermarq.py" --run $1 \
			--iterations $2 --startqbit $3 --shots $4 \
			--increase $5 --method $6 --backend $7


qfw_teardown.sh

