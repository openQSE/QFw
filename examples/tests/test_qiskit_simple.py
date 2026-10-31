from qiskit import QuantumCircuit

import sys

from qfw_example_backend import (
	backend_api_from_argv,
	create_qfw_backend,
	qfw_run_options,
)
from qfw_example_report import emit_result

backend_api = backend_api_from_argv()
nq = int(sys.argv[1])
backend_name = sys.argv[2] if len(sys.argv) > 2 else "nwqsim"
qfw_backend = create_qfw_backend(backend_name, backend_api)

qc = QuantumCircuit(nq)
qc.h(0)
for i in range(nq - 1):
	qc.cx(i, i + 1)
qc.measure_all()

target_shared = None
if backend_api == "v2":
	program = qfw_backend._context.import_program(qc)
	compiled = qfw_backend._context.compile(qfw_backend._route, program)
	target_shared = qfw_backend.target is compiled.target_snapshot
	assert target_shared

print("Default number of shots: 1024")
run_options = qfw_run_options(backend_api)
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
metrics = {
	"counts": counts,
	"statevector_available": statevector is not None,
	"time_taken_sec": getattr(result, "time_taken", None),
}
if backend_api == "v2":
	metrics["target_shared"] = target_shared
emit_result(
	"qiskit-simple",
	parameters={"qubits": nq, "shots": 1024, "backend": backend_name},
	metrics=metrics,
)
