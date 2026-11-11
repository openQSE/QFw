import argparse
import json

import qiskit.qasm2
from qiskit_aer import AerSimulator

from device import fake_backend_from_ibm_records, ibm_device_records


def aer_simulator(mode="ideal", device_id=None, device="CPU",
		  method="automatic", max_memory_mb=0, target_gpus=(),
		  mps_max_bond_dimension=None, mps_truncation_threshold=None,
		  mps_log_data=False):
	options = {"method": method, "device": device,
		   "max_memory_mb": max_memory_mb}
	if target_gpus:
		options["target_gpus"] = list(target_gpus)
	if mps_max_bond_dimension is not None:
		options["matrix_product_state_max_bond_dimension"] = mps_max_bond_dimension
	if mps_truncation_threshold is not None:
		options["matrix_product_state_truncation_threshold"] = mps_truncation_threshold
	if mps_log_data:
		options["mps_log_data"] = True
	if mode == "noisy":
		backend = fake_backend_from_ibm_records(*ibm_device_records(device_id))
		return AerSimulator.from_backend(backend, **options)
	return AerSimulator(**options)


def sample_counts(qasm, shots, seed=None, **settings):
	return sample(qasm, shots, seed, **settings)["counts"]


def sample(qasm, shots, seed=None, **settings):
	run_options = {"shots": shots}
	if seed is not None:
		run_options["seed_simulator"] = seed
	circuit = qiskit.qasm2.loads(
		qasm, custom_instructions=qiskit.qasm2.LEGACY_CUSTOM_INSTRUCTIONS)
	result = aer_simulator(**settings).run(circuit, **run_options).result()
	if not result.success:
		raise RuntimeError(result.status)
	return {"counts": result.get_counts(),
		"diagnostics": result.results[0].metadata}


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
	parser.add_argument("--mps-max-bond-dimension", type=int)
	parser.add_argument("--mps-truncation-threshold", type=float)
	parser.add_argument("--mps-log-data", action="store_true")
	args = parser.parse_args()
	with open(args.qasm_file, encoding="utf-8") as stream:
		qasm = stream.read()
	result = sample(
		qasm, args.shots, args.seed, mode=args.mode, device_id=args.device_id,
		device=args.device, method=args.method,
		max_memory_mb=args.max_memory_mb, target_gpus=args.target_gpus,
		mps_max_bond_dimension=args.mps_max_bond_dimension,
		mps_truncation_threshold=args.mps_truncation_threshold,
		mps_log_data=args.mps_log_data)
	print(json.dumps(result, default=str))


if __name__ == "__main__":
	main()
