import argparse
import json

import qiskit.qasm2
from qiskit_aer import AerSimulator

from device import fake_backend_from_ibm_records, ibm_device_records


def aer_simulator(mode="ideal", device_id=None, device="CPU",
		  method="automatic", max_memory_mb=0, target_gpus=()):
	options = {"method": method, "device": device,
		   "max_memory_mb": max_memory_mb}
	if target_gpus:
		options["target_gpus"] = list(target_gpus)
	if mode == "noisy":
		backend = fake_backend_from_ibm_records(*ibm_device_records(device_id))
		return AerSimulator.from_backend(backend, **options)
	return AerSimulator(**options)


def sample_counts(qasm, shots, seed=None, **settings):
	run_options = {"shots": shots}
	if seed is not None:
		run_options["seed_simulator"] = seed
	circuit = qiskit.qasm2.loads(
		qasm, custom_instructions=qiskit.qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
	result = aer_simulator(**settings).run(circuit, **run_options).result()
	if not result.success:
		raise RuntimeError(result.status)
	return result.get_counts()


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--qasm-file", required=True)
	parser.add_argument("--shots", type=int, required=True)
	parser.add_argument("--seed", type=int)
	parser.add_argument("--mode", choices=("ideal", "noisy"), default="ideal")
	parser.add_argument("--device-id")
	parser.add_argument("--device", choices=("CPU", "GPU"), default="CPU")
	parser.add_argument("--method", default="automatic")
	parser.add_argument("--max-memory-mb", type=int, default=0)
	parser.add_argument("--target-gpus", type=int, nargs="*", default=())
	args = parser.parse_args()
	with open(args.qasm_file, encoding="utf-8") as stream:
		qasm = stream.read()
	counts = sample_counts(
		qasm, args.shots, args.seed, mode=args.mode, device_id=args.device_id,
		device=args.device, method=args.method,
		max_memory_mb=args.max_memory_mb, target_gpus=args.target_gpus)
	print(json.dumps({"counts": counts}))


if __name__ == "__main__":
	main()
