from qiskit import QuantumCircuit

import sys

from qfw_qiskit import QFwBackend
from qfw_example_context import qfw_reservation_options
from qfw_example_report import emit_result

nq = int(sys.argv[1])
backend_name = sys.argv[2] if len(sys.argv) > 2 else "nwqsim"
qfw_backend = QFwBackend(provider=backend_name)

qc = QuantumCircuit(nq)
qc.h(0)
for i in range(nq - 1):
	qc.cx(i, i + 1)
qc.measure_all()

print("Default number of shots: 1024")
run_options = qfw_reservation_options()
job = qfw_backend.run(qc, **run_options)
result = job.result()
counts = result.get_counts(qc)

print(counts)
try:
	statevector = result.get_statevector(qc)
except Exception:
	statevector = None
if statevector is not None:
	print(statevector)
emit_result(
	"qiskit-simple",
	parameters={"qubits": nq, "shots": 1024, "backend": backend_name},
	metrics={
		"counts": counts,
		"statevector_available": statevector is not None,
		"time_taken_sec": getattr(result, "time_taken", None),
	},
)
