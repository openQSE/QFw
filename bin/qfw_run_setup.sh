#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

hostname=$(hostname)
export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_AGENT_NAME=qfw_setup
export DEFW_LISTEN_PORT=8095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes
export DEFW_AGENT_NAME=qfw_setup_phase_2.1
export DEFW_LOG_DIR=$HOME/QFwTmp/${DEFW_AGENT_NAME}_${hostname}
export QFW_DVM_URI_PATH=$HOME/QFwTmp/prte_dvm/dvm-uri

source $HOME/QFwTmp/venv/bin/activate

echo "THI IS THE VNI $SLINGSHOT_VNIS"
python3 $QFW_PATH/bin/qfw_setup.py --groups "$1" \
	--use "/sw/frontier/qhpc/modules/" --mods "quantum/qsim" \
	--python-env "$HOME/QFwTmp/venv/bin/activate"

