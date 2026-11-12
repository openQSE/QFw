import argparse
import base64
import io
import json
import logging
import os
import sys
import traceback

SERVICES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROVIDER = "proxy"
QPY = "qpy"
LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def load_circuit(envelope):
	if envelope["format"] == QPY:
		from qiskit import qpy
		return qpy.load(io.BytesIO(base64.b64decode(envelope["data"])))[0]
	from qiskit import qasm2
	return qasm2.loads(envelope["data"])


def run(job, device_id, max_memory_mb):
	sys.path.insert(0, SERVICES_DIR)
	from util.proxy_sampler.device import load_emulated_device
	from util.proxy_sampler.jobs import sample_job
	from util.proxy_sampler.sampler import ProxySampler

	sampler = ProxySampler(load_emulated_device(device_id), max_memory_mb=max_memory_mb)
	logical = job.get("logical_circuit")
	return sample_job(
		sampler, load_circuit(job["circuit"]), int(job["num_shots"]), job.get("seed_simulator"),
		logical=None if logical is None else load_circuit(logical), bases=job.get("bases"))


def plain(value):
	return value.item() if hasattr(value, "item") else str(value)


def one_line(error):
	return " ".join(f"{type(error).__name__}: {error}".split())


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--job-file", required=True)
	parser.add_argument("--log-file", required=True)
	parser.add_argument("--device-id", required=True)
	parser.add_argument("--max-memory-mb", type=int, required=True)
	args = parser.parse_args()
	logging.basicConfig(filename=args.log_file, level=logging.INFO, format=LOG_FORMAT)
	try:
		with open(args.job_file, encoding="utf-8") as stream:
			job = json.load(stream)
		result = run(job, args.device_id, args.max_memory_mb)
	except Exception as error:
		print(f"{PROVIDER}: {one_line(error)}", flush=True)
		print(json.dumps({PROVIDER: {"error": str(error), "error_type": type(error).__name__}}), flush=True)
		traceback.print_exc()
		sys.exit(1)
	print(json.dumps(result, default=plain))


if __name__ == "__main__":
	main()
