from qiskit import QuantumCircuit

import sys

from qfw_qiskit import QFwBackend, QFwBackendType, QFwBackendCapability
qfw_nwqsim_qiskit_backend = QFwBackend(
	betype=QFwBackendType.QFW_TYPE_NWQSIM,
	capability=QFwBackendCapability.QFW_CAP_STATEVECTOR)

nq = int(sys.argv[1])

qc = QuantumCircuit(nq)
qc.h(0)
for i in range(nq - 1):
	qc.cx(i, i + 1)
qc.measure_all()

print("Default number of shots: 1024")
job = qfw_nwqsim_qiskit_backend.run(qc)
result = job.result()
counts = result.get_counts(qc)

print(counts)
print(result.get_statevector(qc))
