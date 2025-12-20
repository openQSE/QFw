#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

module list

set -xe

qfw_setup.sh

#python3 linear_solver_vqls.py -case sample-tridiag -casefile input_vars.yaml -s 100000 -ep 10
#python3 linear_solver_vqls_gradient-free.py -case sample-tridiag -casefile input_vars.yaml -s 100000 -ep 10
qfw_srun.sh "$QFW_PATH/../applications/qlsa-vqls-qfw/linear_solver_vqls_gradient-free.py"  \
	-case $1 -casefile $2 -s $3 -ep $4


qfw_teardown.sh

