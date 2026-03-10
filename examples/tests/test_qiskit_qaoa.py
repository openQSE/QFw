import networkx as nx
import matplotlib.pyplot as plt
import time
from qiskit.primitives import BackendSampler
from qiskit_algorithms import QAOA
from qiskit_algorithms.optimizers import COBYLA
from qiskit_optimization.algorithms import MinimumEigenOptimizer
from qiskit_optimization.applications.max_cut import Maxcut


from qiskit_aer import AerSimulator
# ------------------ QFW simulator ------------------ #
from qfw_qiskit import QFwBackend, QFwBackendType, QFwBackendCapability
# --------------------------------------------------- #

import sys
sim_type = sys.argv[1]

# Define problem graph
graph = [(0, 1), (0, 2), (1, 2), (1, 3), (2, 3)]

# Create a Maxcut object and get the corresponding QUBO
maxcut = Maxcut(graph)

# G
G = nx.Graph()
G.add_edges_from(graph)

# Plot the graph
plt.figure(figsize=(8, 6))
pos = nx.spring_layout(G)
nx.draw(G, pos, with_labels=True, node_color='lightblue', node_size=2000, edge_color='black', linewidths=1, font_size=15)
plt.title('Graph for Max-Cut Problem')
# Save the plot to a file
plt.savefig('max_cut_graph.png')
# plt.show()

# ------------------ AER simulator ------------------ #
# simulator_obj = AerSimulator(method="statevector")
# --------------------------------------------------- #
# ------------------ QFW simulator ------------------ #
if sim_type.lower() == "nwqsim":
	simulator_obj = QFwBackend(betype=QFwBackendType.QFW_TYPE_NWQSIM, capability=QFwBackendCapability.QFW_CAP_STATEVECTOR)
elif sim_type.lower() == "tnqvm":
	simulator_obj = QFwBackend(betype=QFwBackendType.QFW_TYPE_TNQVM)
else:
	raise ValueError(f"Unsupported simulator type: {sim_type}")
# --------------------------------------------------- #


backend_sampler = BackendSampler(
    backend = simulator_obj,
    skip_transpilation = False,
    options = {"shots": 1024}
)

# Define QAOA
qaoa_mes = QAOA(
    sampler = backend_sampler,
    optimizer = COBYLA(),
    initial_point = [0.0, 1.0]
)

# Solve the problem using QAOA
qaoa_optimizer = MinimumEigenOptimizer(qaoa_mes)
qp = maxcut.to_quadratic_program()
start_time = time.time()
qaoa_result = qaoa_optimizer.solve(qp)
end_time = time.time()
# Print results
print("Time Taken: ", (end_time-start_time)*1000, " ms")
print("Objective value:", qaoa_result.fval)
print("Optimal solution:", qaoa_result.x)
