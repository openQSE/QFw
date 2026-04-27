#!/bin/bash

if [ "${1:-}" == "print_intro" ]; then
	echo "Welcome to the Quantum Framework"
fi

hostname=$(hostname)

unset QFW_HET_GROUP
for var in "${!SLURM_JOB_NODELIST_HET_GROUP_@}"; do
	export QFW_HET_GROUP=1
	break
done

if [ -n "${SLURM_JOB_ID:-}" ]; then
	export QFW_JOB_ID=$SLURM_JOB_ID
else
	export QFW_JOB_ID=-1
fi

export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_AGENT_NAME=qfw_setup
export DEFW_LISTEN_PORT=8095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_LOG_DIR=/tmp/${DEFW_AGENT_NAME}_${hostname}
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes

source $QFW_SETUP_PATH/qfw_lib_path.sh

PYTHONPATH=$PYTHONPATH:$QFW_SETUP_PATH python3 -c "from qfw_venv import setup_qfw_symlinks; setup_qfw_symlinks()"
rc=$?

unset DEFW_CONFIG_PATH
unset DEFW_SHELL_TYPE
unset DEFW_AGENT_NAME
unset DEFW_LISTEN_PORT
unset DEFW_AGENT_TYPE
unset DEFW_LOG_LEVEL
unset DEFW_LOG_DIR
unset DEFW_LOAD_NO_INIT
unset DEFW_ONLY_LOAD_MODULE
unset DEFW_DISABLE_RESMGR

if [[ $rc -ne 0 ]]; then
	echo "Command failed, exiting."
	return 1 2>/dev/null || exit 1
fi
