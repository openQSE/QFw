import argparse
import json
import os
import sys
import time

from defw_app_util import defw_get_directory_service
from defw_exception import DEFwError
from qfw_qiskit.qpm_resolver import QPMResolver
from qfw_qiskit.qpm_selection import qpm_selection_for_provider


def resolve_qpm(provider, timeout):
	selection = qpm_selection_for_provider(provider, default_provider=provider)
	dirsvc = defw_get_directory_service()
	resolver = QPMResolver.from_environment(dirsvc=dirsvc)
	return resolver.connect(
		service_type="qfw.qpm",
		binding_name="default",
		qpm_type=selection["qpm_type"],
		qpm_capabilities=selection["qpm_capabilities"],
		provider=selection["provider"],
		timeout=timeout,
	)


def emit(kind, **payload):
	record = {
		"schema": "qfw-example-reservation-v1",
		"kind": kind,
		"timestamp_ns": time.time_ns(),
	}
	record.update(payload)
	print("QFW_EXAMPLE_RESERVATION " + json.dumps(record, sort_keys=True),
	      flush=True)


def allocation_id():
	for name in ("SLURM_JOB_ID", "SLURM_JOBID", "QFW_ALLOCATION_ID"):
		value = os.environ.get(name)
		if value:
			return value
	return f"qfw-example-{os.getpid()}"


def reserve(args):
	qpm = resolve_qpm(args.backend, args.timeout)
	alloc_id = allocation_id()
	request = {
		"owner": {"user": os.environ.get("USER", "qfw-example")},
		"job_id": alloc_id,
		"allocation_id": alloc_id,
		"num_qubits": args.qubits,
		"walltime_ns": max(1, args.walltime) * 1_000_000_000,
		"ttl_ns": max(args.walltime + 60, args.ttl) * 1_000_000_000,
		"workload": {
			"example": args.example,
			"backend": args.backend,
			"operation": args.operation,
		},
		"run_context": {"operation": args.operation},
		"task_class": {
			"count": args.count,
			"qubit_count": args.qubits,
			"shots": args.shots,
			"measurement_count": args.qubits,
		},
	}
	decision = qpm.reserve(request=request)
	emit("reserve", backend=args.backend, request=request, decision=decision)
	if decision.get("status") != "accepted" or not decision.get(
			"reservation_id"):
		raise DEFwError(f"reservation was not accepted: {decision}")
	return 0


def release(args):
	qpm = resolve_qpm(args.backend, args.timeout)
	result = qpm.release(reservation_id=args.reservation_id, reason=args.reason)
	emit(
		"release",
		backend=args.backend,
		reservation_id=args.reservation_id,
		result=result,
	)
	return 0


def build_parser():
	parser = argparse.ArgumentParser()
	subparsers = parser.add_subparsers(dest="command", required=True)

	reserve_parser = subparsers.add_parser("reserve")
	reserve_parser.add_argument("--backend", required=True)
	reserve_parser.add_argument("--example", required=True)
	reserve_parser.add_argument("--qubits", type=int, required=True)
	reserve_parser.add_argument("--shots", type=int, default=1024)
	reserve_parser.add_argument("--count", type=int, default=1)
	reserve_parser.add_argument("--operation", default="async_run")
	reserve_parser.add_argument("--walltime", type=int, default=300)
	reserve_parser.add_argument("--ttl", type=int, default=600)
	reserve_parser.add_argument("--timeout", type=float, default=40.0)
	reserve_parser.set_defaults(func=reserve)

	release_parser = subparsers.add_parser("release")
	release_parser.add_argument("--backend", required=True)
	release_parser.add_argument("--reservation-id", required=True)
	release_parser.add_argument("--reason", type=int, default=0)
	release_parser.add_argument("--timeout", type=float, default=40.0)
	release_parser.set_defaults(func=release)

	return parser


def main(argv=None):
	args = build_parser().parse_args(argv)
	return args.func(args)


if __name__ == "__main__":
	sys.exit(main())
