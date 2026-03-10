#!/bin/bash

set -xe

qfw_setup.sh

qfw_srun.sh "$QFW_PATH/examples/tests/test_init_qfw.py"

qfw_teardown.sh

