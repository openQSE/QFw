import argparse
import sys

from defw import me
from qfw_qiskit import QFwBackend, QFwBackendType, QFwBackendCapability
from qfw_example_report import emit_result


def parse_args():
	parser = argparse.ArgumentParser(description="QFw backend init smoke test")
	parser.add_argument(
		"case",
		choices=(
			"nwqsim-statevector",
			"tnqvm-tensor",
			"tnqvm-default",
			"qb-missing",
		),
	)
	return parser.parse_args()


def create_backend(case):
	if case == "nwqsim-statevector":
		return QFwBackend(
			betype=QFwBackendType.QFW_TYPE_NWQSIM,
			capability=QFwBackendCapability.QFW_CAP_STATEVECTOR,
		)
	if case == "tnqvm-tensor":
		return QFwBackend(
			betype=QFwBackendType.QFW_TYPE_TNQVM,
			capability=QFwBackendCapability.QFW_CAP_TENSORNETWORK,
		)
	if case == "tnqvm-default":
		return QFwBackend(betype=QFwBackendType.QFW_TYPE_TNQVM)
	if case == "qb-missing":
		return QFwBackend(
			betype=QFwBackendType.QFW_TYPE_QB,
			lookup_timeout=3,
		)
	raise AssertionError(case)


def main():
	args = parse_args()
	backend = None
	error = None
	try:
		backend = create_backend(args.case)
	except Exception as exc:
		error = str(exc)
		print(f"Got an Exception {exc}")

	expected_missing = args.case == "qb-missing"
	ok = (error is not None) if expected_missing else (backend is not None)

	emit_result(
		"init-test",
		status="ok" if ok else "error",
		parameters={"case": args.case},
		metrics={"backend_created": backend is not None},
		details={"error": error} if error else {},
	)
	if backend is not None:
		try:
			me.exit()
		except SystemExit:
			pass
	return 0 if ok else 1


sys.exit(main())
