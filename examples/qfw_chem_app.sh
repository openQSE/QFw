#!/bin/bash

set -xe

qfw_setup.sh

# takes the name of the chemistry app script to run
qfw_srun.sh "$QFW_PATH/examples/tests/chemistry_example_aim2/$1"

qfw_teardown.sh

