#!/bin/bash

# Startup:
#  - The Simulation Environment Components
#     - PRTE DVM
#     - Resource Manager
#     - DEFw Launcher
#     - QPM
#
#  - The Application side Interface
#     - QTM

services_config="${QFW_SETUP_PATH}/qfw_services.yaml"

while [[ $# -gt 0 ]]; do
	case "$1" in
		--services-config)
			if [[ $# -lt 2 ]]; then
				echo "--services-config requires a path" >&2
				exit 1
			fi
			services_config="$2"
			shift 2
			;;
		-h|--help)
			echo "Usage: qfw_setup.sh [--services-config <yaml>]"
			exit 0
			;;
		*)
			echo "Unknown argument: $1" >&2
			exit 1
			;;
	esac
done

hostname=$(hostname)
het_groups=$($QFW_SETUP_PATH/qfw_extract_groups.sh) || exit 1
source "$QFW_SETUP_PATH/qfw_run_tmp.sh"
qfw_create_run_tmp || exit 1
echo "QFw run logs: ${QFW_RUN_TMP_PATH}"

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
#  running on will need to setup the environment manually. That setup is
#  now encapsulated by qfw_activate before the process is started.
#
#  3. The actual QPM service code shouldn't really worry about having to
#  set up the environment for the QRC. The QRC should've been spawned
#  under the launcher, and the launcher should have the correct
#  environment; therefore, the QRC will have the correct inherited
#  environment as well.
export QFW_DVM_URI_PATH=$QFW_RUN_TMP_PATH/prte_dvm/dvm-uri
export DEFW_AGENT_NAME=qfw_setup_phase_1
export DEFW_LOG_DIR=$QFW_RUN_TMP_PATH/${DEFW_AGENT_NAME}_${hostname}
export DEFW_SHELL_TYPE=cmdline
export DEFW_LISTEN_PORT=9095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_DISABLE_RESMGR=yes
export DEFW_PY_LOGLEVEL=debug,DEFW_ALL

echo "*******START PHASE ONE SETUP: PRTE*******"
python3 $QFW_SETUP_PATH/qfw_setup.py --dvm --groups "$het_groups"
setup_rc=$?
unset DEFW_AGENT_NAME
unset DEFW_LOG_DIR
unset DEFW_SHELL_TYPE
unset DEFW_LISTEN_PORT
unset DEFW_AGENT_TYPE
unset DEFW_LOG_LEVEL
unset DEFW_DISABLE_RESMGR
unset DEFW_PY_LOGLEVEL
if [ $setup_rc -ne 0 ]; then
	echo "Failed to setup Quantum Framework"
	exit -1
fi
echo "*******COMPLETED PHASE ONE SETUP: PRTE*******"

echo "*******START PHASE TWO SETUP*******"
export DEFW_AGENT_NAME=qfw_setup_phase_2
export DEFW_LOG_DIR=$QFW_RUN_TMP_PATH/${DEFW_AGENT_NAME}_${hostname}
export DEFW_SHELL_TYPE=cmdline
export DEFW_LISTEN_PORT=9095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_DISABLE_RESMGR=yes
export DEFW_PY_LOGLEVEL=debug,DEFW_ALL
python3 $QFW_SETUP_PATH/qfw_setup.py --prun --groups "$het_groups" \
	--services-config "$services_config" &
unset DEFW_AGENT_NAME
unset DEFW_LOG_DIR
unset DEFW_SHELL_TYPE
unset DEFW_LISTEN_PORT
unset DEFW_AGENT_TYPE
unset DEFW_LOG_LEVEL
unset DEFW_DISABLE_RESMGR
unset DEFW_PY_LOGLEVEL
echo "*******COMPLETED PHASE TWO SETUP*******"

echo "Quantum Framework Initialized"
