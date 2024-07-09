#!/bin/bash

source $QFW_PATH/bin/qfw_venv.sh print_intro

# Startup:
#  - The Simulation Environment Components
#     - PRTE DVM
#     - Resource Manager
#     - DEFw Launcher
#     - QPM
#
#  - The Application side Interface
#     - QTM

het_groups=$(env | grep "SLURM_JOB_NODELIST_HET_GROUP_")

echo $DEFW_LOG_DIR

# we need to propagate the environment to the other nodes. That's why
# we're explicitly using srun. We can't run the dvm with srun, so we
# separated these two operations into two steps
# 
# Using srun will execute qfw_run_setup.sh which calls qfw_setup.py on
# each of the het-group 1 nodes. qfw_setup.py will only do something
# useful on the head node. TODO: We can put -n 1 which should run it only
# on 1 node. However, at this point I'm not sure if this will work with
# the current code. So let's keep it this way.
#
# qfw_setup.py will start the resmgr, a Launcher on each node, a QPM on
# the current node and a QTM on the group 0 head node.
#
# There is some implication with regards to the environment we have to
# consider:
#
#  1. Since anything started by qfw_setup.py on the node which
#  qfw_setup.py is running on will inherit the environment, we don't have
#  to manually propagate the environment. This is true for the resmgr, QPM
#  and one of the Launchers.
#
#  2. Anything spawned on a node other than the one qfw_setup.py is
#  running on will need to setup the environment manually. This is why we
#  pass the module use path, and modules to load and the python
#  environment to activate. These will precede the actual process which
#  will run and therefore the process will have the correct environment.
#
#  3. The actual QPM service code shouldn't really worry about having to
#  set up the environment for the QRC. The QRC should've been spawned
#  under the launcher, and the launcher should have the correct
#  environment; therefore, the QRC will have the correct inherited
#  environment as well.
export QFW_DVM_URI_PATH=$HOME/QFwTmp/prte_dvm/dvm-uri
export DEFW_AGENT_NAME=qfw_setup_phase_1
export DEFW_LOG_DIR=$HOME/QFwTmp/${DEFW_AGENT_NAME}_${hostname}
#srun --het-group=1 qfw_run_setup.sh "$het_groups" &
#srun --het-group=1 dump_info.sh

# NOTE: We can't run this with srun, because within this script we start
# a PRTE DVM and it conflicts with srun
#srun --het-group 1 -N 1 -n 1 qfw_run_setup_p2.sh "$het_groups"

#echo "*******START PHASE ONE SETUP: PRTE*******"
#python3 $QFW_PATH/bin/qfw_setup.py --dvm --groups "$het_groups" \
#		--use "/sw/frontier/qhpc/modules/" --mods "quantum/qsim"
#if [ $? -ne 0 ]; then
#	echo "Failed to setup Quantum Framework"
#	exit -1
#fi
#echo "*******COMPLETED PHASE ONE SETUP: PRTE*******"

echo "*******START PHASE TWO SETUP*******"
export DEFW_AGENT_NAME=qfw_setup_phase_2
export DEFW_LOG_DIR=$HOME/QFwTmp/${DEFW_AGENT_NAME}_${hostname}
python3 $QFW_PATH/bin/qfw_setup.py --prun --groups "$het_groups" \
			--use "/sw/frontier/qhpc/modules/" --mods "quantum/qsim" &
echo "*******COMPLETED PHASE TWO SETUP*******"

echo "Quantum Framework Initialized"

