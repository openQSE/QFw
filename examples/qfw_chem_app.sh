#!/bin/bash

set -xe

qfw_setup.sh

qfw_srun.sh "$QFW_PATH/../applications/chemistry_example_aim2/$1"

qfw_teardown.sh

