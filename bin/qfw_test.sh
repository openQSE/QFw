#!/bin/bash

module use /sw/frontier/qhpc/modules/
module load quantum/qsim

uname -a
echo "# START-TIME: $(date)"
echo "#            SLURM_NNODES: $SLURM_NNODES"
echo "#            SLURM_NPROCS: $SLURM_NPROCS"
echo "#             SLURM_JOBID: $SLURM_JOBID"
echo "# SLURM_JOB_CPUS_PER_NODE: $SLURM_JOB_CPUS_PER_NODE"
echo "#  SLURM_THREADS_PER_CORE: $SLURM_THREADS_PER_CORE"
echo "#----"

module list

echo "##################################"

set -xe

qfw_setup.sh

sleep 10

filtered_env=$(env | grep "SLURM_JOB_NODELIST_HET_GROUP_1")
run_application.sh $filtered_env

#qfw_teardown.sh

echo "# RC=$?"
echo "#########"

echo "# END-TIME: $(date)"

