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

timestamp=$(date +"%Y%m%d_%H%M%S")
random_string=$(uuidgen | tr '[:upper:]' '[:lower:]' | head -c 8)
dir_name="qfwtmp_${timestamp}_${random_string}"
export QFW_TMP_DIR_PATH=$QFW_TMP_PATH/${dir_name}

qfw_setup.sh

qfw_srun.sh $QFW_PATH/../applications/QCNO/tests/main_vac_osc_qfw_latest.py $@

qfw_teardown.sh

echo "# RC=$?"
echo "#########"

echo "# END-TIME: $(date)"

