#!/bin/bash

source $QFW_SETUP_PATH/qfw_activate

hostname=$(hostname)
export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_AGENT_NAME=qfw_setup
export DEFW_LISTEN_PORT=8095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes
export DEFW_AGENT_NAME=qfw_setup_phase_2.1
export DEFW_LOG_DIR=$QFW_TMP_PATH/${DEFW_AGENT_NAME}_${hostname}
export QFW_DVM_URI_PATH=$QFW_TMP_PATH/prte_dvm/dvm-uri

set -x
source $QFW_SETUP_PATH/qfw_lib_path.sh
python3 $QFW_SETUP_PATH/qfw_setup.py --groups "$1"

