#!/bin/bash

usage() {
    echo "Usage: $0 <sim_qrc> <n_nodes> <n_processes_per_node>"
    echo
    echo "Arguments:"
    echo "  <sim_qrc>                 : 'nwqsim' or 'tnqvm' or 'aer' "
    echo "  <n_nodes>                 : Number of nodes (only for logging properly; has to be changed in svc_qpm.py)"
    echo "  <n_processes_per_node>    : Number of processes per node (only for logging properly; has to be changed in svc_qpm.py)"
    exit 1
}
if [ $# -ne 3 ]; then
    echo "Error: Incorrect number of arguments."
    usage
fi

# QRC type
SIM_QRC=$1
# MPI and node configuration
N_NODES=$2                    # Number of nodes
N_PROCESSES_PER_NODE=$3       # Number of processes per node

# Print out the configuration
echo "QRC: $SIM_QRC"
echo "Number of nodes: $N_NODES"
echo "Number of processes per node: $N_PROCESSES_PER_NODE"


# Submit SLURM job
sbatch <<EOT
#!/bin/bash

#SBATCH --output=/lustre/orion/gen008/proj-shared/qhpc/qhpc_srikar/QFw/results_analysis/qlstm_${SIM_QRC}_${N_NODES}_${N_PROCESSES_PER_NODE}_%j.out

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

# this uses qiskit-aer
./experiment_qlstm.sh

# this uses our QFW backend
# ./experiment_qlstm.sh --qfw_backend ${SIM_QRC}

# ./test_pennylane.sh ${SIM_QRC}

EOT