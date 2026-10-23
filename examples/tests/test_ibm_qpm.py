import sys

from qiskit.circuit import QuantumCircuit, ParameterVector

from qfw_qiskit import QFwBackend
from qfw_example_context import qfw_reservation_options
from qfw_example_report import emit_result

shots = int(sys.argv[1]) if len(sys.argv) > 1 else 1024
backend_name = sys.argv[2] if len(sys.argv) > 2 else "shim"
qfw_backend = QFwBackend(provider=backend_name)

# 3-qubit parameterized circuit
th = ParameterVector("th", 3)
qc = QuantumCircuit(3, 3)
qc.ry(th[0], 0)
qc.ry(th[1], 1)
qc.ry(th[2], 2)
qc.cx(0, 1)
qc.cx(1, 2)
qc.measure([0, 1, 2], [0, 1, 2])

param_values = {str(th[0]): 0.1, str(th[1]): 0.2, str(th[2]): 0.3}
compilation_options = {"optimization_level": 1}

print(f"Running 3-qubit parameterized circuit, {shots} shots, backend={backend_name}")
run_options = qfw_reservation_options()
run_options["shots"] = shots
run_options["param_values"] = param_values
run_options["compilation_options"] = {"optimization_level": 1}
job = qfw_backend.run(qc, **run_options)
result = job.result()
counts = result.get_counts(qc)

print(counts)
emit_result(
	"qiskit-qsa",
	parameters={
		"qubits": 3,
		"shots": shots,
		"backend": backend_name,
		"param_values": param_values,
		"compilation_options": compilation_options,
	},
	metrics={
		"counts": counts,
		"time_taken_sec": getattr(result, "time_taken", None),
	},
)
