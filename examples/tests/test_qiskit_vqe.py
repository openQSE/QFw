import sys
import time
import math
from mpi4py import MPI
from qiskit import transpile
from qiskit.quantum_info import SparsePauliOp
from qiskit.circuit.library import TwoLocal
from qiskit_nature.second_q.drivers import PySCFDriver
from qiskit_nature.second_q.transformers import FreezeCoreTransformer
from qiskit_nature.second_q.mappers import ParityMapper
from qiskit_nature.second_q.problems import ElectronicStructureProblem
from scipy.optimize import minimize
import numpy as np

# ------------------ QFW Backend -------------------- #
from qfw_qiskit import QFwBackend, QFwBackendType, QFwBackendCapability
# --------------------------------------------------- #
backend = QFwBackend(betype=QFwBackendType.QFW_TYPE_NWQSIM, capability=QFwBackendCapability.QFW_CAP_STATEVECTOR)
#backend = Aer.get_backend('statevector_simulator')

# Initialize MPI
comm = MPI.COMM_WORLD
rank = comm.Get_rank()
size = comm.Get_size()

try:
	max_iter = int(sys.argv[1])
except (ValueError, IndexError):
	max_iter = 50

total_itr = 0
start_time = time.time()

# Define the molecule
molecule = "H .0 .0 .0; H .0 .0 0.74"
driver = PySCFDriver(molecule)
q_molecule = driver.run()

# Freeze core orbitals and reduce the problem size
transformer = FreezeCoreTransformer()
transformed_q_molecule = transformer.transform(q_molecule)
electronic_structure_problem = ElectronicStructureProblem(transformed_q_molecule.hamiltonian)

# Map the molecular Hamiltonian to a qubit operator
mapper = ParityMapper()
qubit_op = mapper.map(electronic_structure_problem.second_q_ops()[0])

# Partition the Hamiltonian (Pauli terms) across MPI ranks
pauli_list = qubit_op.paulis
coefficients = qubit_op.coeffs
num_paulis = len(pauli_list)
paulis_per_rank = math.ceil(num_paulis / size)
start_index = rank * paulis_per_rank
end_index = (rank + 1) * paulis_per_rank if rank != size - 1 else num_paulis
sub_paulis = pauli_list[start_index:end_index]
sub_coeffs = coefficients[start_index:end_index]

# Create the sub-Hamiltonian for this rank
sub_hamiltonian = SparsePauliOp(sub_paulis, sub_coeffs)
print(f"Rank {rank} of {size} calculationg sub_hamiltonian(size: {len(sub_hamiltonian)}):\n\t{sub_hamiltonian}")

# Define the variational form and optimizer
ansatz = TwoLocal(4, rotation_blocks='ry', entanglement_blocks='cx', reps=2)


# Function to run VQE manually
def run_vqe(params):
	global total_itr
	# Create a parameterized quantum state
	circuit = ansatz.assign_parameters(params)
	# Transpile the circuit for the given backend
	transpiled_circuit = transpile(circuit, backend)
	total_itr += 1
	# run the transpiled circuit
	job = backend.run(transpiled_circuit)
	result = job.result()
	# get the statevector and try compute expectation value
	statevector = result.get_statevector(transpiled_circuit)
	ex = statevector.expectation_value(sub_hamiltonian)
	if np.iscomplexobj(ex):
		ex = np.real(ex)
	return ex


# Initial parameters
initial_params = np.random.random(ansatz.num_parameters)
result = minimize(run_vqe, initial_params, method="COBYLA", options={'maxiter': max_iter})

# Gather the optimized energy for each sub-Hamiltonian
sub_energy = result.fun
all_energies = comm.gather(sub_energy, root=0)

if rank == 0:
	# Combine energies (summing the energies for simplicity)
	final_energy = np.sum(all_energies)
	print(f"\n\nTotal time = {time.time() - start_time}")
	print(f"Total number of circuit execution: {total_itr * size}")
	print(f"Sub-energies: {all_energies}")
	print(f"Combined ground state energy: {final_energy}")
