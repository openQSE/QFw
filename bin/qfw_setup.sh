#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_AGENT_NAME=qfw_setup
export DEFW_LISTEN_PORT=8095
export DEFW_NO_RESMGR=yes
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_LOG_DIR=/tmp/${DEFW_AGENT_NAME}_${hostname}
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

hostname=$(hostname)
het_groups=$(env | grep "SLURM_JOB_NODELIST_HET_GROUP_")

# we need to propagate the environment to the other nodes. That's why
# we're explicitly using srun. We can't run the dvm with srun, so we
# separated these two operations into two steps
echo "*******START PHASE ONE SETUP*******"
srun --het-group=1 qfw_run_setup.sh "$het_groups"
#srun --het-group=1 dump_info.sh
echo "*******COMPLETED PHASE ONE SETUP*******"
if [ $? -ne 0 ]; then
	echo "Failed to setup Quantum Framework"
	exit -1
fi

export DEFW_AGENT_NAME=qfw_setup_phase_2
export DEFW_LOG_DIR=$HOME/QFwTmp/${DEFW_AGENT_NAME}_${hostname}

# NOTE: We can't run this with srun, because within this script we start
# a PRTE DVM and it conflicts with srun
#python3 $QFW_PATH/bin/qfw_setup.py --dvm --groups "$het_groups" \
#         --use "/sw/frontier/qhpc/modules/" --mods "quantum/qsim"
#echo "*******COMPLETED PHASE TWO SETUP*******"
#if [ $? -ne 0 ]; then
#	echo "Failed to setup Quantum Framework"
#	exit -1
#fi

echo "Quantum Framework Initialized"

