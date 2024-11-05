#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

module list

set -xe

qfw_setup.sh

qfw_run.sh "$QFW_PATH/../applications/test_qfw_init.py"

qfw_teardown.sh

