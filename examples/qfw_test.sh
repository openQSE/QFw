#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

module list

export QFW_VENV_PATH=/ccs/home/shehataa/tmp/qfw_venv

set -xe

qfw_setup.sh

qfw_srun.sh "$QFW_PATH/../applications/test_qfw_init.py"

qfw_teardown.sh

