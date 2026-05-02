#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${QFW_SETUP_PATH:-}" ]]; then
	export QFW_SETUP_PATH="${SCRIPT_DIR}"
fi

if [[ -z "${_QFW_ACTIVE:-}" ]]; then
	source $QFW_SETUP_PATH/qfw_activate --skip-patches
fi

source "$QFW_SETUP_PATH/qfw_run_tmp.sh"
qfw_use_current_run_tmp || exit 1

hostname=$(hostname)
export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_LISTEN_PORT=8095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes
export DEFW_AGENT_NAME=qfw_teardown
export DEFW_LOG_DIR=$QFW_RUN_TMP_PATH/${DEFW_AGENT_NAME}_${hostname}

source $QFW_SETUP_PATH/qfw_lib_path.sh

filtered_env=$($QFW_SETUP_PATH/qfw_extract_groups.sh)
python3 $QFW_SETUP_PATH/qfw_setup.py --shutdown --groups "$filtered_env"
teardown_rc=$?
qfw_clear_current_run_tmp || true
exit $teardown_rc
