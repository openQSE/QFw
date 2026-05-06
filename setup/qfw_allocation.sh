#!/bin/bash

qfw_detect_allocation() {
	local host
	local force=false

	if [[ "${1:-}" == "--force" ]]; then
		force=true
	fi

	host="$(hostname)"

	if ! ${force} &&
	   [[ -n "${QFW_ALLOCATION_MODE:-}" &&
		  -n "${QFW_GROUP_0_NODELIST:-}" &&
		  -n "${QFW_GROUP_1_NODELIST:-}" &&
		  -n "${QFW_GROUPS:-}" ]]; then
		return 0
	fi

	if compgen -A variable SLURM_JOB_NODELIST_HET_GROUP_ >/dev/null; then
		local group0="${SLURM_JOB_NODELIST_HET_GROUP_0:-}"
		local group1="${SLURM_JOB_NODELIST_HET_GROUP_1:-}"

		if [[ -z "${group0}" || -z "${group1}" ]]; then
			echo "QFw requires Slurm heterogeneous groups 0 and 1" >&2
			return 1
		fi

		export QFW_ALLOCATION_MODE=heterogeneous
		export QFW_GROUP_0_NODELIST="${group0}"
		export QFW_GROUP_1_NODELIST="${group1}"
		export QFW_GROUPS="GROUP_0=${group0}:GROUP_1=${group1}"
	elif [[ -n "${SLURM_JOB_NODELIST:-}" ]]; then
		export QFW_ALLOCATION_MODE=slurm
		export QFW_GROUP_0_NODELIST="${SLURM_JOB_NODELIST}"
		export QFW_GROUP_1_NODELIST="${SLURM_JOB_NODELIST}"
		export QFW_GROUPS="GROUP_0=${SLURM_JOB_NODELIST}:GROUP_1=${SLURM_JOB_NODELIST}"
	else
		export QFW_ALLOCATION_MODE=local
		export QFW_GROUP_0_NODELIST="${host}"
		export QFW_GROUP_1_NODELIST="${host}"
		export QFW_GROUPS="GROUP_0=${host}:GROUP_1=${host}"
	fi
}

qfw_group_head() {
	local node_list="$1"
	python3 "${QFW_SETUP_PATH}/extract_head_node.py" "QFW_NODELIST=${node_list}"
}
