from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator

import sys
import time

# ------------------ QFW backend--------------------- #
from qfw_qiskit import QFwBackend, QFwBackendType, QFwBackendCapability
# --------------------------------------------------- #

nq = int(sys.argv[1])
sim_type = sys.argv[2]
itrs = int(sys.argv[3])

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
		simulator_obj = QFwBackend(betype=QFwBackendType.QFW_TYPE_NWQSIM, capability=QFwBackendCapability.QFW_CAP_STATEVECTOR)
		# counts_nwqsim = qfw.execute(qc, shots=1024, backend="nwqsim") # sync
		qfw_job = simulator_obj.run(qc, shots=1024)  # async job, but will poll and get result
		res_obj = qfw_job.result()
		counts_nwqsim = res_obj.get_counts()
		end_time = time.time()
		print("\n\n OVERALL TIME TAKEN (", (end_time - start_time) * 1000, ") ms \n", "Output: ", counts_nwqsim, "\n\n")
		print("\n\n QFW with NWQSIM took (", (res_obj.time_taken) * 1000, ") ms \n", "Output: ", counts_nwqsim, "\n\n")

elif sim_type == "tnqvm":
	# ghz_tnqvm_times = []
	for i in range(itrs):
		start_time = time.time()
		simulator_obj = QFwBackend(
			betype=QFwBackendType.QFW_TYPE_TNQVM,
			capability=QFwBackendCapability.QFW_CAP_TENSORNETWORK)
		qfw_job = simulator_obj.run(qc, shots=1024)  # async job, but will poll and get result
		res_obj = qfw_job.result()
		counts_tnqvm = res_obj.get_counts()
		end_time = time.time()
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
		counts_tnqvm = res_obj.get_counts()
		end_time = time.time()
		# ghz_nwqsim_times.append((end_time - start_time)*1000)
		print("\n\n Qiskit-AER took (", (res_obj.time_taken) * 1000, ") ms \n", "Output: ", counts_nwqsim, "\n\n")
