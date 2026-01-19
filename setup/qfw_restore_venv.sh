#!/bin/bash

export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_LISTEN_PORT=8095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_LOG_DIR=/tmp/${DEFW_AGENT_NAME}_${hostname}
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes
export DEFW_AGENT_NAME=qfw_restore_venv

source $QFW_SETUP_PATH/qfw_lib_path.sh

PYTHONPATH=$PYTHONPATH:$QFW_SETUP_PATH $python_path -c "from qfw_venv import restore_symlinks; restore_symlinks()"


