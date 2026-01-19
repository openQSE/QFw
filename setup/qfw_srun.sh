#!/bin/bash

echo "RUNNING APPLICATION"

source $QFW_SETUP_PATH/qfw_lib_path.sh

hostname=$(hostname)
export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_AGENT_NAME=ExtractInfo
export DEFW_LISTEN_PORT=10095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=error
export DEFW_LOG_DIR=/tmp/${DEFW_AGENT_NAME}_${hostname}
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes

filtered_env=$(env | grep "SLURM_JOB_NODELIST_HET_GROUP_1")
output=$(python3 $QFW_SETUP_PATH/extract_head_node.py $filtered_env)

node=$(echo "$output" | tr '\n' ' ' | \
	/usr/bin/python3 -c "import sys;print(sys.stdin.read().split()[0])")

echo "resource manager is located on: ****$node****"

filename=$(basename "$1" | cut -f 1 -d '.')

export DEFW_AGENT_NAME=${filename}_${hostname}
export DEFW_LISTEN_PORT=9600
export DEFW_PARENT_HOSTNAME=$node
export DEFW_PARENT_PORT=8090
export DEFW_PARENT_NAME=resmgr
export DEFW_AGENT_TYPE=agent
export DEFW_SHELL_TYPE=cmdline
export DEFW_LOG_LEVEL=error
export DEFW_LOG_DIR=/tmp/${DEFW_AGENT_NAME}
export DEFW_ONLY_LOAD_MODULE=api_qpm
export DEFW_DISABLE_RESMGR=no
export DEFW_PREF_PATH=${QFW_TMP_PATH}/defw_app_pref.yaml

set -xe
srun --het-group=0 python3 $1 "${@:2}"
