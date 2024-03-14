#!/bin/bash

# By creating an environment using defwp I basically make python3 an alias
# to defwp
defwp -m venv venv
source venv/bin/activate
pip3 install -r requirements.txt

hostname=$(hostname)
resmgr_node=$(echo "$1" | cut -d',' -f1)

export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_AGENT_NAME=qtm_$hostname
export DEFW_LISTEN_PORT=9100
export DEFW_AGENT_TYPE=agent
export DEFW_PARENT_HOSTNAME=$resmgr_node
export DEFW_PARENT_PORT=8090
export DEFW_PARENT_NAME=resmgr
export DEFW_SHELL_TYPE=cmdline
export DEFW_LOG_LEVEL=all
export DEFW_LOG_DIR=$HOME/tmp/$DEFW_AGENT_NAME
export DEFW_ONLY_LOAD_MODULE=api_qpm

mpirun python3 /sw/frontier/qhpc/QFw/qtm/qtm.py
