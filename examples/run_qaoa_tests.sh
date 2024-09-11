#!/bin/bash

usage() {
    echo "Usage: $0 <sim_qrc> <QUBO_size> <n_itrs> <n_nodes> <n_processes_per_node>"
    echo
    echo "Arguments:"
    echo "  <sim_qrc>                 : 'nwqsim' or 'tnqvm'"
    echo "  <QUBO_size>               : QUBO Size, choose from 2, 4, 8, 10, 16, 20, 30, 40, 50, 60, 70, 80, 90, 100. From files under QFw/tests/dr_kim_qaoa/qubo_files/"
    echo "  <n_itrs>                  : Number of iterations"
    echo "  <n_nodes>                 : Number of nodes (only for logging properly; has to be changed in svc_qpm.py)"
    echo "  <n_processes_per_node>    : Number of processes per node (only for logging properly; has to be changed in svc_qpm.py)"
    exit 1
}
if [ $# -ne 5 ]; then
    echo "Error: Incorrect number of arguments."
    usage
fi

SIM_QRC=$1
QUBO_SIZE=$2
N_ITRS=$3

# MPI and node configuration
N_NODES=$4                    # Number of nodes
N_PROCESSES_PER_NODE=$5       # Number of processes per node

# Print out the configuration
echo "QRC: $SIM_QRC"
echo "QUBO SIZE: $QUBO_SIZE"
echo "Number of iterations: $N_ITRS"
echo "Number of nodes: $N_NODES"
echo "Number of processes per node: $N_PROCESSES_PER_NODE"

# Submit SLURM job
sbatch <<EOT
#!/bin/bash

#SBATCH --output=/lustre/orion/gen008/proj-shared/qhpc/qhpc_srikar/QFw/results_analysis/qaoa_${SIM_QRC}_${QUBO_SIZE}_${N_NODES}_${N_PROCESSES_PER_NODE}_%j.out

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
./experiment_qaoa.sh $QUBO_SIZE $SIM_QRC $N_ITRS

EOT