from qfw_qiskit import QFwBackend, QFwBackendType, QFwBackendCapability
nwqsim = QFwBackend(betype=QFwBackendType.QFW_TYPE_NWQSIM, capability=QFwBackendCapability.QFW_CAP_STATEVECTOR)
tnqvm = QFwBackend(betype=QFwBackendType.QFW_TYPE_TNQVM, capability=QFwBackendCapability.QFW_CAP_TENSORNETWORK)
tnqvm2 = QFwBackend(betype=QFwBackendType.QFW_TYPE_TNQVM)

try:
	qb = QFwBackend(betype=QFwBackendType.QFW_TYPE_QB)
except Exception as e:
	qb = None
	print(f"Got an Exception {e}")
print(f"backends created: {nwqsim}, {tnqvm}, {tnqvm}, {qb}")

if nwqsim:
	nwqsim.shutdown()
if tnqvm:
	tnqvm.shutdown()
if tnqvm2:
	tnqvm2.shutdown()
if qb:
	qb.shutdown()
