#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

source $QFW_PATH/bin/qfw_venv.sh
export DEFW_AGENT_NAME=qfw_teardown
export DEFW_LOG_DIR=/tmp/${DEFW_AGENT_NAME}_${hostname}

filtered_env=$(env | grep "SLURM_JOB_NODELIST_HET_GROUP_")
which python3
python3 $QFW_PATH/bin/qfw_setup.py --shutdown --groups "$filtered_env"


