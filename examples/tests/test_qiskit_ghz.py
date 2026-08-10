from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

import sys
import time

# ------------------ QFW backend--------------------- #
from qfw_qiskit import QFwBackend
from qfw_example_context import qfw_reservation_options
from qfw_example_report import emit_result
# --------------------------------------------------- #

nq = int(sys.argv[1])
sim_type = sys.argv[2]
itrs = int(sys.argv[3])
records = []
qfw_run_options = (
	qfw_reservation_options()
	if sim_type in ("nwqsim", "tnqvm") else {}
)

qc = QuantumCircuit(nq)
qc.h(0)
for i in range(nq - 1):
	qc.cx(i, i + 1)
qc.measure_all()
# qc.draw()
# print(qc)


if sim_type == "nwqsim":
	# ghz_nwqsim_times = []
	for i in range(itrs):
		start_time = time.time()
		simulator_obj = QFwBackend(provider="nwqsim")
		# counts_nwqsim = qfw.execute(qc, shots=1024, backend="nwqsim") # sync
		qfw_job = simulator_obj.run(qc, shots=1024, **qfw_run_options)  # async job, but will poll and get result
		res_obj = qfw_job.result()
		counts_nwqsim = res_obj.get_counts()
		end_time = time.time()
		records.append({
			"iteration": i,
			"backend": "nwqsim",
			"overall_time_ms": (end_time - start_time) * 1000,
			"backend_time_ms": getattr(res_obj, "time_taken", 0) * 1000,
			"counts": counts_nwqsim,
		})
		print("\n\n OVERALL TIME TAKEN (", (end_time - start_time) * 1000, ") ms \n", "Output: ", counts_nwqsim, "\n\n")
		print("\n\n QFW with NWQSIM took (", (res_obj.time_taken) * 1000, ") ms \n", "Output: ", counts_nwqsim, "\n\n")

elif sim_type == "tnqvm":
	# ghz_tnqvm_times = []
	for i in range(itrs):
		start_time = time.time()
		simulator_obj = QFwBackend(provider="tnqvm")
		qfw_job = simulator_obj.run(qc, shots=1024, **qfw_run_options)  # async job, but will poll and get result
		res_obj = qfw_job.result()
		counts_tnqvm = res_obj.get_counts()
		end_time = time.time()
		records.append({
			"iteration": i,
			"backend": "tnqvm",
			"overall_time_ms": (end_time - start_time) * 1000,
			"backend_time_ms": getattr(res_obj, "time_taken", 0) * 1000,
			"counts": counts_tnqvm,
		})
		# ghz_nwqsim_times.append((end_time - start_time)*1000)
		print("\n\n OVERALL TIME TAKEN (", (end_time - start_time) * 1000, ") ms \n", "Output: ", counts_tnqvm, "\n\n")
		print("\n\n QFW with TNQVM took (", (res_obj.time_taken) * 1000, ") ms \n", "Output: ", counts_tnqvm, "\n\n")

elif sim_type == "qiskit-aer":
	# ghz_tnqvm_times = []
	for i in range(itrs):
		start_time = time.time()
		simulator_obj = AerSimulator(method="statevector")
		aer_job = simulator_obj.run(qc, shots=1024)  # async job, but will poll and get result
		res_obj = aer_job.result()
		counts_aer = res_obj.get_counts()
		end_time = time.time()
		records.append({
			"iteration": i,
			"backend": "qiskit-aer",
			"overall_time_ms": (end_time - start_time) * 1000,
			"backend_time_ms": getattr(res_obj, "time_taken", 0) * 1000,
			"counts": counts_aer,
		})
		# ghz_nwqsim_times.append((end_time - start_time)*1000)
		print("\n\n Qiskit-AER took (", (res_obj.time_taken) * 1000, ") ms \n", "Output: ", counts_aer, "\n\n")
else:
	raise ValueError(f"Unsupported simulator type: {sim_type}")

emit_result(
	"ghz-qiskit",
	parameters={
		"qubits": nq,
		"backend": sim_type,
		"iterations": itrs,
		"shots": 1024,
	},
	metrics={"iterations": records},
)
