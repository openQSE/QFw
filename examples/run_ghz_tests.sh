#!/bin/bash

usage() {
    echo "Usage: $0 <frontend> <sim_qrc> <n_qubits> <n_itrs> <n_nodes> <n_processes_per_node>"
    echo
    echo "Arguments:"
    echo "  <frontend>                : 'qiskit' or 'pennylane'"
    echo "  <sim_qrc>                 : 'nwqsim' or 'tnqvm'"
    echo "  <n_qubits>                : Number of qubits"
    echo "  <n_itrs>                  : Number of iterations"
    echo "  <n_nodes>                 : Number of nodes (only for logging properly; has to be changed in svc_qpm.py)"
    echo "  <n_processes_per_node>    : Number of processes per node (only for logging properly; has to be changed in svc_qpm.py)"
    exit 1
}
if [ $# -ne 6 ]; then
    echo "Error: Incorrect number of arguments."
    usage
fi

FRONTEND=$1
SIM_QRC=$2
N_QUBITS=$3
N_ITRS=$4
N_NODES=$5
N_PROCESSES_PER_NODE=$6

if [[ "$FRONTEND" != "qiskit" && "$FRONTEND" != "pennylane" ]]; then
    echo "Error: Invalid frontend '$FRONTEND'. Must be 'qiskit' or 'pennylane'."
    usage
fi

# Print out the configuration
echo "Frontend: $FRONTEND"
echo "QRC: $SIM_QRC"
echo "Number of qubits: $N_QUBITS"
echo "Number of iterations: $N_ITRS"
echo "Number of nodes: $N_NODES"
echo "Number of processes per node: $N_PROCESSES_PER_NODE"


# Submit SLURM job
sbatch <<EOT
#!/bin/bash

#SBATCH --output=/lustre/orion/gen008/proj-shared/qhpc/qhpc_srikar/QFw/results_analysis/${FRONTEND}_ghz_${SIM_QRC}_${N_QUBITS}_${N_NODES}_${N_PROCESSES_PER_NODE}_%j.out

# job component 1
#SBATCH -A GEN008_borg
#SBATCH -N 1
#SBATCH -t 2:00:00

#SBATCH hetjob

# job component 2
#SBATCH -A GEN008_borg
#SBATCH -N ${N_NODES}
#SBATCH -t 2:00:00

cd /lustre/orion/gen008/proj-shared/qhpc/qhpc_srikar/QFw/bin
./experiment_ghz.sh $FRONTEND $N_QUBITS $SIM_QRC $N_ITRS

EOT