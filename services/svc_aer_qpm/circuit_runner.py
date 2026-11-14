import argparse
import json
import sys
import traceback

import qiskit.qasm2
from qiskit.circuit.equivalence_library import SessionEquivalenceLibrary
from qiskit.circuit.library import get_standard_gate_name_mapping
from qiskit.transpiler import PassManager
from qiskit.transpiler.passes import BasisTranslator, UnrollCustomDefinitions
from qiskit_aer import AerSimulator

from device import device_backend


def aer_simulator(mode="ideal", device_id=None, **options):
	if mode == "noisy":
		return AerSimulator.from_backend(device_backend(device_id), **options)
	return AerSimulator(**options)


def translate_to_simulator_gates(circuit, simulator):
	standard = get_standard_gate_name_mapping()
	basis_gates = [name for name in simulator.configuration().basis_gates
		if name in standard]
	return PassManager([
		UnrollCustomDefinitions(
			SessionEquivalenceLibrary, basis_gates=basis_gates),
		BasisTranslator(SessionEquivalenceLibrary, basis_gates),
	]).run(circuit)


def sample_counts(qasm, shots, seed=None, **settings):
	return sample(qasm, shots, seed, **settings)["counts"]


def sample(qasm, shots, seed=None, mode="ideal", device_id=None, **options):
	circuit = qiskit.qasm2.loads(
		qasm, custom_instructions=qiskit.qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
	simulator = aer_simulator(mode, device_id, **options)
	if mode == "ideal":
		circuit = translate_to_simulator_gates(circuit, simulator)
	run_options = {"shots": shots}
	if seed is not None:
		run_options["seed_simulator"] = seed
	result = simulator.run(circuit, **run_options).result()
	return {"counts": result.get_counts(),
		"diagnostics": result.results[0].metadata}


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--qasm-file", required=True)
	parser.add_argument("--shots", type=int, required=True)
	parser.add_argument("--seed", type=int)
	parser.add_argument("--settings", type=json.loads, default={})
	args = parser.parse_args()
	with open(args.qasm_file, encoding="utf-8") as stream:
		qasm = stream.read()
	try:
		result = sample(
			qasm, args.shots, args.seed,
			mode=args.settings.get("mode", "ideal"),
			device_id=args.settings.get("device_id"),
			**args.settings.get("options", {}))
	except Exception as error:
		print(f"aer: {one_line(error)}", flush=True)
		traceback.print_exc()
		sys.exit(1)
	print(json.dumps(result, default=str))


def one_line(error):
	return " ".join(f"{type(error).__name__}: {error}".split())


if __name__ == "__main__":
	main()
