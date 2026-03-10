#!/bin/bash

set -xe

qfw_setup.sh

# takes the number of VQE iterations
qfw_srun.sh "$QFW_PATH/examples/tests/test_qiskit_vqe.py" $1

qfw_teardown.sh

