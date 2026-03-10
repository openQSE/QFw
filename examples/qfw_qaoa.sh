#!/bin/bash

set -xe

qfw_setup.sh

# takes the simulator type: nwqsim or tnqvm
qfw_srun.sh "$QFW_PATH/examples/tests/test_qiskit_qaoa.py" $1

qfw_teardown.sh

