#!/bin/bash

if [[ -z "${_QFW_ACTIVE:-}" ]]; then
	source $QFW_SETUP_PATH/qfw_activate --skip-patches
fi

groups="$1"
services_config="${2:-${QFW_SETUP_PATH}/qfw_services.yaml}"
hostname=$(hostname)
export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_LISTEN_PORT=8095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_PY_LOGLEVEL=debug,DEFW_ALL
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes
export DEFW_AGENT_NAME=qfw_setup_phase_2.1
export DEFW_LOG_DIR=$QFW_TMP_PATH/${DEFW_AGENT_NAME}_${hostname}
export QFW_DVM_URI_PATH=$QFW_TMP_PATH/prte_dvm/dvm-uri

set -x
source $QFW_SETUP_PATH/qfw_lib_path.sh
python3 $QFW_SETUP_PATH/qfw_setup.py --groups "$groups" \
	--services-config "$services_config"

unset DEFW_AGENT_NAME
unset DEFW_LOG_DIR
unset DEFW_SHELL_TYPE
unset DEFW_LISTEN_PORT
unset DEFW_AGENT_TYPE
unset DEFW_LOG_LEVEL
unset DEFW_DISABLE_RESMGR
unset DEFW_PY_LOGLEVEL
unset DEFW_LOAD_NO_INIT
unset DEFW_ONLY_LOAD_MODULE
