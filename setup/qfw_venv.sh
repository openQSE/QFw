#!/bin/bash

if [ "$1" == "print_intro" ]; then
	echo "Welcome to the Quantum Framework"
fi

hostname=$(hostname)

# Loop through all environment variables
for var in "${!SLURM_JOB_NODELIST_HET_GROUP_@}"; do
	export QFW_HET_GROUP=1
	break
done

# Check if SLURM_JOB_ID is set
if [ -n "${SLURM_JOB_ID}" ]; then
	# If SLURM_JOB_ID is set, assign it to QFW_JOB_ID
	export QFW_JOB_ID=$SLURM_JOB_ID
else
	# If SLURM_JOB_ID is not set, assign -1 to QFW_JOB_ID
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

if [[ $? -ne 0 ]]; then
	echo "Command failed, exiting."
	exit 1
fi

