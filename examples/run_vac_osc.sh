#!/bin/bash

SIM_QRC=$1
N_QUBITS=$2

# MPI and node configuration
N_NODES=$3                   # Number of nodes
N_PROCESSES_PER_NODE=$4       # Number of processes per node

# Print out the configuration
echo "QRC: $SIM_QRC"
echo "Number of nodes: $N_NODES"
echo "Number of processes per node: $N_PROCESSES_PER_NODE"


# Submit SLURM job
sbatch <<EOT
#!/bin/bash

#SBATCH --output=/lustre/orion/gen008/proj-shared/qhpc/qhpc_srikar/QFw/results_analysis/vacosc_${SIM_QRC}_${N_QUBITS}_${N_NODES}_${N_PROCESSES_PER_NODE}_%j.out

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
./experiment_vac_osc.sh ${SIM_QRC} ${N_QUBITS} ${N_NODES} ${N_PROCESSES_PER_NODE}

EOT
