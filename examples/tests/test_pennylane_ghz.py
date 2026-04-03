import pennylane as qml
import sys
import time

from qfw_qiskit import QFwBackend, QFwBackendType, QFwBackendCapability


def run_simulation(dev, itrs):
	@qml.qnode(dev)
	def circuit():
		qml.Hadamard(0)
		for i in range(nq - 1):
			qml.CNOT(wires=[i, i + 1])
		return qml.counts()

	for i in range(itrs):
		start_time = time.time()
		result = circuit()
		end_time = time.time()

		print(f"\n\n OVERALL TIME TAKEN ({(end_time - start_time) * 1000:.2f}) ms")
		print("Output:", result)
		print("\n\n")


nq = int(sys.argv[1])
sim_type = sys.argv[2]
itrs = int(sys.argv[3])


if sim_type == "nwqsim":
	backend_instance = QFwBackend(
		betype=QFwBackendType.QFW_TYPE_NWQSIM,
		capability=QFwBackendCapability.QFW_CAP_STATEVECTOR)
elif sim_type == "tnqvm":
	backend_instance = QFwBackend(
		betype=QFwBackendType.QFW_TYPE_TNQVM,
		capability=QFwBackendCapability.QFW_CAP_TENSORNETWORK)
elif sim_type == "qiskit-aer":
	backend_instance = qml.device('qiskit.aer', wires=nq)
else:
	raise ValueError("Unknown simulation type")

dev = qml.device('qiskit.remote', wires=nq, backend=backend_instance, shots=1024)

run_simulation(dev, itrs)
