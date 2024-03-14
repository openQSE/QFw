#!/bin/bash

export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_AGENT_NAME=qfw_setup
export DEFW_LISTEN_PORT=8095
export DEFW_NO_RESMGR=yes
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_LOG_DIR=/tmp/$DEFW_AGENT_NAME_$hostname
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes

# By creating an environment using defwp I basically make python3 an alias
# to defwp
echo "Creating QFw Virtual Environment"
defwp -m venv --without-pip $HOME/QFwTmp/venv
echo "Activating QFw Virtual Environment"
source $HOME/QFwTmp/venv/bin/activate

# Startup:
#  - The Simulation Environment Components
#     - PRTE DVM
#     - Resource Manager
#     - DEFw Launcher
#     - QPM
#
#  - The Application side Interface
#     - QTM

filtered_env=$(env | grep "SLURM_JOB_NODELIST_HET_GROUP_")

qfw_run_setup.sh "$filtered_env"

if [ $? -ne 0 ]; then
	echo "Failed to setup Quantum Framework"
	exit -1
fi

echo "Quantum Framework Initialized"
