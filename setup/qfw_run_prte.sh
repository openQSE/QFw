#!/bin/bash

set -euo pipefail

export SLURM_JOBID="$3"
export SLURM_JOB_ID="$3"

PRTE_ARGS=(
    --host "$2"
    --report-uri "$1/dvm-uri"
    -x "SLURM_JOB_ID=$3"
    -x "SLURM_JOBID=$3"
    --prtemca ras ^slurm
    --prtemca plm slurm
    --prtemca plm_slurm_verbose 100
    --prtemca plm_base_verbose 100
    --prtemca ras_base_verbose 100
    --prtemca plm_slurm_args "--het-group 1"
)

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
