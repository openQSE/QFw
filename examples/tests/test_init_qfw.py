import argparse
import sys

from defw import me
from qfw_qiskit import QFwBackend
from qfw_example_report import emit_result


def parse_args():
	parser = argparse.ArgumentParser(description="QFw backend init smoke test")
	parser.add_argument("backend")
	return parser.parse_args()


def main():
	args = parse_args()
	backend = None
	error = None
	try:
		backend = QFwBackend(provider=args.backend)
	except Exception as exc:
		error = str(exc)
		print(f"Got an Exception {exc}")

	ok = backend is not None

	emit_result(
		"init-test",
		status="ok" if ok else "error",
		parameters={"backend": args.backend},
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
