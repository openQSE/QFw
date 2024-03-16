#!/bin/bash

source $HOME/QFwTmp/venv/bin/activate

hostname=$(hostname)
export DEFW_AGENT_NAME=qfw_setup_phase_1
export DEFW_LOG_DIR=$HOME/QFwTmp/${DEFW_AGENT_NAME}_${hostname}

python3 $QFW_PATH/bin/qfw_setup.py --groups "$1"

sleep 300
