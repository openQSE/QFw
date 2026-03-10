#!/bin/bash

set -xe

qfw_setup.sh

# Takes number of qubits
#  ex: test_qiskit_simple.py 10
qfw_srun.sh "$QFW_PATH/examples/tests/test_qiskit_simple.py" $1

qfw_teardown.sh

