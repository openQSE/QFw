import argparse
import sys

import numpy as np
from qiskit import QuantumCircuit

from defw import me
from qfw_qiskit import QFwBackend
from qfw_example_context import qfw_reservation_options
from qfw_example_report import emit_result


def parse_args():
	parser = argparse.ArgumentParser(
		description="Check a QPM's returned statevector against the known "
			"GHZ amplitudes, not just that something came back")
	parser.add_argument("num_qubits", type=int)
	parser.add_argument("backend", nargs="?", default="nwqsim")
	parser.add_argument("--tolerance", type=float, default=1e-6)
	return parser.parse_args()


def ghz_statevector(num_qubits):
	expected = np.zeros(2 ** num_qubits, dtype=complex)
	expected[0] = 2 ** -0.5
	expected[-1] = 2 ** -0.5
	return expected


def main():
	args = parse_args()

	qc = QuantumCircuit(args.num_qubits)
	qc.h(0)
	for i in range(args.num_qubits - 1):
		qc.cx(i, i + 1)
	qc.measure_all()

	backend = QFwBackend(provider=args.backend)
	run_options = qfw_reservation_options()
	job = backend.run(qc, **run_options)
	result = job.result()
	statevector = np.asarray(result.get_statevector(qc), dtype=complex)

	expected = ghz_statevector(args.num_qubits)
	max_error = float(np.max(np.abs(statevector - expected)))
	ok = max_error < args.tolerance

	emit_result(
		"statevector-correctness",
		status="ok" if ok else "error",
		parameters={
			"qubits": args.num_qubits,
			"backend": args.backend,
			"tolerance": args.tolerance,
		},
		metrics={
			"max_amplitude_error": max_error,
			"statevector": [str(a) for a in statevector],
		},
	)
	try:
		me.exit()
	except SystemExit:
		pass
	return 0 if ok else 1


sys.exit(main())
