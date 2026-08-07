#!/bin/bash

echo "RUNNING APPLICATION"

usage() {
	echo "Usage: qfw_srun.sh [--load-modules <modules>] <script> [args...]"
}

load_modules="${QFW_SRUN_LOAD_MODULES:-api_qpm}"

while [[ $# -gt 0 ]]; do
	case "$1" in
		--load-modules)
			if [[ $# -lt 2 ]]; then
				echo "--load-modules requires a module list" >&2
				exit 1
			fi
			load_modules="$2"
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			break
			;;
	esac
done

if [[ $# -lt 1 ]]; then
	usage >&2
	exit 1
fi

source $QFW_SETUP_PATH/qfw_lib_path.sh
source "$QFW_SETUP_PATH/qfw_allocation.sh"
qfw_detect_allocation --force || exit 1
source "$QFW_SETUP_PATH/qfw_launch.sh"
source "$QFW_SETUP_PATH/qfw_run_tmp.sh"
qfw_use_current_run_tmp || exit 1

hostname=$(hostname)
export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_AGENT_NAME=ExtractInfo
export DEFW_LISTEN_PORT=10095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=error
export DEFW_LOG_DIR=$QFW_RUN_TMP_PATH/${DEFW_AGENT_NAME}_${hostname}
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_dirsvc
export DEFW_DISABLE_DIRSVC=yes

node=$(qfw_group_head "${QFW_GROUP_1_NODELIST}")

echo "directory service is located on: ****$node****"

filename=$(basename "$1" | cut -f 1 -d '.')

export DEFW_AGENT_NAME=${filename}_${hostname}
export DEFW_LISTEN_PORT=9600
export DEFW_PARENT_HOSTNAME=$node
export DEFW_PARENT_PORT=8090
export DEFW_PARENT_NAME=dirsvc_${node}
export DEFW_AGENT_TYPE=agent
export DEFW_SHELL_TYPE=cmdline
export DEFW_LOG_LEVEL=error
export DEFW_LOG_DIR=$QFW_RUN_TMP_PATH/${DEFW_AGENT_NAME}
export DEFW_ONLY_LOAD_MODULE=$load_modules
export DEFW_DISABLE_DIRSVC=no
export DEFW_PY_LOGLEVEL=debug,DEFW_ALL

set -xe
qfw_launch_app python3 "$1" "${@:2}"
app_rc=$?

set +xe
unset DEFW_AGENT_NAME
unset DEFW_LOG_DIR
unset DEFW_SHELL_TYPE
unset DEFW_LISTEN_PORT
unset DEFW_AGENT_TYPE
unset DEFW_LOG_LEVEL
unset DEFW_DISABLE_DIRSVC
unset DEFW_PY_LOGLEVEL
exit $app_rc
