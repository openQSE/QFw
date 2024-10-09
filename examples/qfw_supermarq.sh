#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

module list

set -xe

qfw_setup.sh

run_application.sh "$QFW_PATH/../applications/test_supermarq.py" --run $1 --iterations $2 --startqbit $3 --increase $4 --method $5

qfw_teardown.sh

