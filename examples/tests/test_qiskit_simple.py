from qiskit import QuantumCircuit

import sys

from qfw_qiskit import QFwBackend
from qfw_example_context import qfw_reservation_options
from qfw_example_report import emit_result

qfw_nwqsim_qiskit_backend = QFwBackend(provider="nwqsim")

nq = int(sys.argv[1])

qc = QuantumCircuit(nq)
qc.h(0)
for i in range(nq - 1):
	qc.cx(i, i + 1)
qc.measure_all()

print("Default number of shots: 1024")
run_options = qfw_reservation_options()
job = qfw_nwqsim_qiskit_backend.run(qc, **run_options)
result = job.result()
counts = result.get_counts(qc)

print(counts)
print(result.get_statevector(qc))
emit_result(
	"qiskit-simple",
	parameters={"qubits": nq, "shots": 1024, "backend": "nwqsim"},
	metrics={
		"counts": counts,
		"time_taken_sec": getattr(result, "time_taken", None),
	},
)
