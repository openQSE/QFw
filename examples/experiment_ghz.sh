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

# takes - #qubits, simtype and #itrs
echo "Running $1 for $2 #qubits with $3 for $4 itrs"
if [[ $1 == "qiskit" ]]; then
    qfw_srun.sh $QFW_PATH/../applications/test_ghz_qfw_qiskit.py $2 $3 $4
elif [[ $1 == "pennylane" ]]; then
    qfw_srun.sh $QFW_PATH/../applications/test_ghz_qfw_pennylane.py $2 $3 $4
else
    echo "Error: Unknown option $1"
    exit 1
fi

qfw_teardown.sh

echo "# RC=$?"
echo "#########"

echo "# END-TIME: $(date)"

