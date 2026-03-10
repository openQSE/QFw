#!/bin/bash

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

# takes:
#   number of qubits
#   simtype: nwqsim, tnqvm or qiskit-aer
#   number of iterations
#
echo "Running $1 for $2 #qubits with $3 for $4 itrs"
if [[ $1 == "qiskit" ]]; then
    qfw_srun.sh $QFW_PATH/examples/tests/test_qiskit_ghz.py $2 $3 $4
elif [[ $1 == "pennylane" ]]; then
    qfw_srun.sh $QFW_PATH/examples/tests/test_pennylane_ghz.py $2 $3 $4
else
    echo "Error: Unknown option $1"
    exit 1
fi

qfw_teardown.sh

echo "# RC=$?"
echo "#########"

echo "# END-TIME: $(date)"

