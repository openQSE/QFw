#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

source $QFW_SETUP_PATH/qfw_venv.sh
export DEFW_AGENT_NAME=qfw_teardown
export DEFW_LOG_DIR=/tmp/${DEFW_AGENT_NAME}_${hostname}

filtered_env=$($QFW_SETUP_PATH/qfw_extract_groups.sh)
which python3
python3 $QFW_SETUP_PATH/qfw_setup.py --shutdown --groups "$filtered_env"


