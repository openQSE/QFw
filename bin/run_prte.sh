#!/bin/bash
export SLURM_JOBID=$3
export SLURM_JOB_ID=$3
echo "mkdir -p $1; prte --host $2 --report-uri $1/dvm-uri -x SLURM_JOB_ID=$3 -x SLURM_JOBID=$3 --prtemca ras ^slurm --prtemca plm slurm --prtemca plm_slurm_verbose 100 --prtemca plm_base_verbose 100 --prtemca ras_base_verbose 100 --prtemca plm_slurm_args '--het-group 1'"

#mkdir -p $1; prte --host $2 --report-uri $1/dvm-uri -x SLURM_JOB_ID=$3 -x SLURM_JOBID=$3 --prtemca ras ^slurm --prtemca plm slurm --prtemca plm_slurm_verbose 100 --prtemca plm_base_verbose 100 --prtemca ras_base_verbose 100 --prtemca plm_slurm_args "--het-group 1" --daemonize

# Below command has more debugs
mkdir -p $1; prte --debug-daemons -v --debug-daemons-file --host $2 --report-uri $1/dvm-uri -x SLURM_JOB_ID=$3 -x SLURM_JOBID=$3 --prtemca ras ^slurm --prtemca plm slurm --prtemca plm_slurm_verbose 100 --prtemca plm_base_verbose 100 --prtemca ras_base_verbose 100 --prtemca pmix_server_verbose 100  --prtemca odls_base_verbose 100 --prtemca prte_state_base_verbose 100 --prtemca plm_slurm_args "--het-group 1"
#--daemonize

