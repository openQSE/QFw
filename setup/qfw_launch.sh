#!/bin/bash

if [[ -z "${QFW_ALLOCATION_MODE:-}" ]]; then
	qfw_detect_allocation || return 1 2>/dev/null || exit 1
fi

qfw_launch_app() {
	case "${QFW_ALLOCATION_MODE}" in
		heterogeneous)
			srun --het-group=0 "$@"
			;;
		slurm)
			srun "$@"
			;;
		local)
			"$@"
			;;
		*)
			echo "Unknown QFW_ALLOCATION_MODE=${QFW_ALLOCATION_MODE}" >&2
			return 1
			;;
	esac
}

qfw_add_prte_runtime_args() {
	local -n args_ref="$1"

	case "${QFW_ALLOCATION_MODE}" in
		heterogeneous)
			args_ref+=(
				--prtemca ras ^slurm
				--prtemca plm slurm
				--prtemca plm_slurm_verbose 100
				--prtemca plm_base_verbose 100
				--prtemca ras_base_verbose 100
				--prtemca plm_slurm_args "--het-group 1"
			)
			;;
		slurm)
			args_ref+=(
				--prtemca ras ^slurm
				--prtemca plm slurm
				--prtemca plm_slurm_verbose 100
				--prtemca plm_base_verbose 100
				--prtemca ras_base_verbose 100
			)
			;;
		local)
			;;
		*)
			echo "Unknown QFW_ALLOCATION_MODE=${QFW_ALLOCATION_MODE}" >&2
			return 1
			;;
	esac
}
