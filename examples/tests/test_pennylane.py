# got this originally from https://docs.pennylane.ai/projects/qiskit/en/latest/devices/aer.html

import pennylane as qml
from qiskit_aer import noise

# ------------------ QFW simulator ------------------ #
from qfw_qiskit import QFwBackend
from qfw_example_report import emit_result
# --------------------------------------------------- #

# Error probabilities
prob_1 = 0.001  # 1-qubit gate
prob_2 = 0.01   # 2-qubit gate

# Depolarizing quantum errors
error_1 = noise.depolarizing_error(prob_1, 1)
error_2 = noise.depolarizing_error(prob_2, 2)

# Add errors to noise model
noise_model = noise.NoiseModel()
noise_model.add_all_qubit_quantum_error(error_1, ['u1', 'u2', 'u3'])
noise_model.add_all_qubit_quantum_error(error_2, ['cx'])

# ---------------------- Qiskit-Aer Pennylane Device ---------------------- #
# dev = qml.device('qiskit.aer', wires=2, noise_model=noise_model)
# -------------------------------------------------------------------------- #
# ---------------------- QFWSimulator Pennylane Device --------------------- #
backend_instance = QFwBackend(provider="nwqsim")
dev = qml.device('qiskit.remote', wires=2, backend=backend_instance, shots=1024)
# -------------------------------------------------------------------------- #


# Create a PennyLane quantum node run on the device
@qml.qnode(dev)
def circuit(x, y, z):
    qml.RZ(z, wires=[0])
    qml.RY(y, wires=[0])
    qml.RX(x, wires=[0])
    qml.CNOT(wires=[0, 1])
    return qml.expval(qml.PauliZ(wires=1))


# Result of noisy simulator
result = circuit(0.2, 0.1, 0.3)
print("\n \t ------------------- \n")
print("Whatever this should be doing = ", result)
print("\n \t ------------------- \n")
emit_result(
	"pennylane",
	parameters={"qubits": 2, "backend": "nwqsim", "shots": 1024},
	metrics={"expectation_value": result},
)
