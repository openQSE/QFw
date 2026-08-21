#!/bin/bash

set -euo pipefail

uname -a
echo "# START-TIME: $(date)"
echo "#            SLURM_NNODES: ${SLURM_NNODES:-}"
echo "#            SLURM_NPROCS: ${SLURM_NPROCS:-}"
echo "#             SLURM_JOBID: ${SLURM_JOBID:-}"
echo "# SLURM_JOB_CPUS_PER_NODE: ${SLURM_JOB_CPUS_PER_NODE:-}"
echo "#  SLURM_THREADS_PER_CORE: ${SLURM_THREADS_PER_CORE:-}"
echo "#----"

module list || true

echo "##################################"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"

qfw_example_begin "ghz-${1:-unknown}" "$@"

# takes:
#   the framework: qiskit or pennylane
#   number of qubits
#   simtype: nwqsim, tnqvm
#   number of iterations
#
#   ex: ./qfw_ghz.sh qiskit 4 nwqsim 4
#
echo "Running $1 for $2 #qubits with $3 for $4 itrs"
if [[ $1 == "qiskit" ]]; then
    qfw_example_setup_backend_service "$3"
    qfw_example_srun_with_backend_reservation \
        "$3" ghz-qiskit "$2" 1024 "$4" async_run \
        "$(qfw_example_path tests/test_qiskit_ghz.py)" "$2" "$3" "$4"
elif [[ $1 == "pennylane" ]]; then
    qfw_example_setup_backend_service "$3"
    qfw_example_srun_with_backend_reservation \
        "$3" ghz-pennylane "$2" 1024 "$4" async_run \
        "$(qfw_example_path tests/test_pennylane_ghz.py)" "$2" "$3" "$4"
else
    echo "Error: Unknown option $1"
    exit 1
fi

echo "# RC=$?"
echo "#########"

echo "# END-TIME: $(date)"
