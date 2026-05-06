#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${QFW_SETUP_PATH:-}" ]]; then
    export QFW_SETUP_PATH="${SCRIPT_DIR}"
fi

source "${QFW_SETUP_PATH}/qfw_allocation.sh"
qfw_detect_allocation
source "${QFW_SETUP_PATH}/qfw_launch.sh"

job_id="${3:-${QFW_JOB_ID:-}}"
if [[ -n "${job_id}" && "${job_id}" != "-1" ]]; then
    export SLURM_JOBID="${job_id}"
    export SLURM_JOB_ID="${job_id}"
fi

PRTE_ARGS=(
    --host "$2"
    --report-uri "$1/dvm-uri"
)

if [[ -n "${SLURM_JOB_ID:-}" ]]; then
    PRTE_ARGS+=(
        -x "SLURM_JOB_ID=${SLURM_JOB_ID}"
        -x "SLURM_JOBID=${SLURM_JOB_ID}"
    )
fi

qfw_add_prte_runtime_args PRTE_ARGS

if [ "$(id -u)" -eq 0 ]; then
    PRTE_ARGS+=(--allow-run-as-root)
fi

echo "mkdir -p $1; prte ${PRTE_ARGS[*]} --daemonize"

mkdir -p "$1"
prte "${PRTE_ARGS[@]}" --daemonize

# --pmixmca pmix_server_spawn_verbose 100 --pmixmca pmix_client_spawn_verbose 100
# Below command has more debugs
# mkdir -p "$1"; prte --debug-daemons --debug-daemons-file "${PRTE_ARGS[@]}" \
#     --prtemca pmix_server_verbose 100 --prtemca odls_base_verbose 100 \
#     --prtemca prte_state_base_verbose 100 --daemonize
