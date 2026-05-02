#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${QFW_SETUP_PATH:-}" ]]; then
	export QFW_SETUP_PATH="${SCRIPT_DIR}"
fi

hostname=$(hostname)
if [[ -f "$QFW_SETUP_PATH/qfw_run_tmp.sh" ]]; then
	source "$QFW_SETUP_PATH/qfw_run_tmp.sh"
	qfw_use_current_run_tmp >/dev/null 2>&1 || true
fi
qfw_log_base="${QFW_RUN_TMP_PATH:-/tmp}"

export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_LISTEN_PORT=8095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_AGENT_NAME=qfw_restore_venv
export DEFW_LOG_DIR=${qfw_log_base}/${DEFW_AGENT_NAME}_${hostname}
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes

source $QFW_SETUP_PATH/qfw_lib_path.sh

restore_python="${QFW_VENV_PATH}/bin/python3_defw_orig"
if [[ ! -x "${restore_python}" ]]; then
	restore_python="${QFW_VENV_PATH}/bin/python3"
fi

PYTHONPATH=$PYTHONPATH:$QFW_SETUP_PATH \
	"${restore_python}" -c \
	"from qfw_venv import restore_symlinks; restore_symlinks()"

unset DEFW_CONFIG_PATH
unset DEFW_SHELL_TYPE
unset DEFW_LISTEN_PORT
unset DEFW_AGENT_TYPE
unset DEFW_LOG_LEVEL
unset DEFW_LOG_DIR
unset DEFW_LOAD_NO_INIT
unset DEFW_ONLY_LOAD_MODULE
unset DEFW_DISABLE_RESMGR
unset DEFW_AGENT_NAME
unset qfw_log_base
