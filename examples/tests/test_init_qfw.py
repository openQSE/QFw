from qfw_qiskit import QFwBackend, QFwBackendType, QFwBackendCapability
from qfw_example_report import emit_result

nwqsim = QFwBackend(betype=QFwBackendType.QFW_TYPE_NWQSIM, capability=QFwBackendCapability.QFW_CAP_STATEVECTOR)
tnqvm = QFwBackend(betype=QFwBackendType.QFW_TYPE_TNQVM, capability=QFwBackendCapability.QFW_CAP_TENSORNETWORK)
tnqvm2 = QFwBackend(betype=QFwBackendType.QFW_TYPE_TNQVM)
qb_error = None

try:
	qb = QFwBackend(betype=QFwBackendType.QFW_TYPE_QB)
except Exception as e:
	qb = None
	qb_error = str(e)
	print(f"Got an Exception {e}")
print(f"backends created: {nwqsim}, {tnqvm}, {tnqvm}, {qb}")

emit_result(
	"init-test",
	metrics={
		"nwqsim_created": nwqsim is not None,
		"tnqvm_created": tnqvm is not None,
		"tnqvm_default_created": tnqvm2 is not None,
		"qb_created": qb is not None,
	},
	details={"qb_error": qb_error} if qb_error else {},
)

if nwqsim:
	nwqsim.shutdown()
if tnqvm:
	tnqvm.shutdown()
if tnqvm2:
	tnqvm2.shutdown()
if qb:
	qb.shutdown()
