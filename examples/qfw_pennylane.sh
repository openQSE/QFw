#!/bin/bash

set -xe

qfw_setup.sh

# Tests pennylane with nwqsim
qfw_srun.sh "$QFW_PATH/examples/tests/test_pennylane.py"

qfw_teardown.sh

