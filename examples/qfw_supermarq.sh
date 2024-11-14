#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

module list

set -xe

qfw_setup.sh

qfw_run.sh "$QFW_PATH/../applications/test_supermarq.py" --run $1 \
			--iterations $2 --startqbit $3 --shots $4 \
			--increase $5 --method $6 --backend $7


qfw_teardown.sh

