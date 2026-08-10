import pennylane as qml
import sys
import time

from qfw_qiskit import QFwBackend
from qfw_example_context import apply_qfw_reservation_to_backend
from qfw_example_report import emit_result


def run_simulation(dev, itrs):
	records = []

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
		records.append({
			"iteration": i,
			"overall_time_ms": (end_time - start_time) * 1000,
			"result": result,
		})

		print(f"\n\n OVERALL TIME TAKEN ({(end_time - start_time) * 1000:.2f}) ms")
		print("Output:", result)
		print("\n\n")
	return records


nq = int(sys.argv[1])
sim_type = sys.argv[2]
itrs = int(sys.argv[3])


if sim_type == "nwqsim":
	backend_instance = apply_qfw_reservation_to_backend(
		QFwBackend(provider="nwqsim"))
elif sim_type == "tnqvm":
	backend_instance = apply_qfw_reservation_to_backend(
		QFwBackend(provider="tnqvm"))
elif sim_type == "qiskit-aer":
	backend_instance = qml.device('qiskit.aer', wires=nq)
else:
	raise ValueError("Unknown simulation type")

dev = qml.device('qiskit.remote', wires=nq, backend=backend_instance, shots=1024)

records = run_simulation(dev, itrs)
emit_result(
	"ghz-pennylane",
	parameters={
		"qubits": nq,
		"backend": sim_type,
		"iterations": itrs,
		"shots": 1024,
	},
	metrics={"iterations": records},
)
