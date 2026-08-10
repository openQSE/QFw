import argparse
import json
import os
import sys
import time

from defw_app_util import defw_get_directory_service
from defw_exception import DEFwError
from qfw_qiskit.qpm_resolver import QPMResolver
from qfw_qiskit.qpm_selection import qpm_selection_for_provider


def normalize_reservation_id(value):
	if isinstance(value, str):
		value = value.strip()
		if value.isdecimal():
			return int(value)
	return value


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


def json_object(value, label):
	if not value:
		return {}
	try:
		parsed = json.loads(value)
	except json.JSONDecodeError as exc:
		raise DEFwError(f"{label} must be a JSON object: {exc}") from exc
	if not isinstance(parsed, dict):
		raise DEFwError(f"{label} must be a JSON object")
	return parsed


def reserve(args):
	qpm = resolve_qpm(args.backend, args.timeout)
	alloc_id = args.allocation_id or allocation_id()
	job_id = args.job_id or alloc_id
	measurement_count = args.measurements
	if measurement_count is None:
		measurement_count = args.qubits
	request = {
		"owner": {
			"user": args.owner or os.environ.get("USER", "qfw-example"),
		},
		"job_id": job_id,
		"allocation_id": alloc_id,
		"num_qubits": args.qubits,
		"workload_kind": args.workload_kind,
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
			"depth": args.depth,
			"one_q_gate_count": args.one_q_gates,
			"two_q_gate_count": args.two_q_gates,
			"shots": args.shots,
			"measurement_count": measurement_count,
		},
	}
	if args.target_device:
		request["target_device_id"] = args.target_device
	if args.scope_id:
		request["scope_id"] = args.scope_id
	request["workload"].update(json_object(args.workload_json, "workload JSON"))
	request["run_context"].update(
		json_object(args.run_context_json, "run-context JSON"))
	request["task_class"].update(
		json_object(args.task_class_json, "task-class JSON"))
	parameters = json_object(args.parameters_json, "parameters JSON")
	if parameters:
		request["parameters"] = parameters
	if args.credential_hint:
		request["credential_hint"] = args.credential_hint
	analytics = json_object(args.analytics_json, "analytics JSON")
	if analytics:
		request["analytics"] = analytics
	credential_hint = json_object(
		args.credential_hint_json, "credential-hint JSON")
	if credential_hint:
		request["credential_hint"] = credential_hint
	if args.credential_handle:
		request["credential_handle"] = args.credential_handle
	if args.credential_scope:
		request["credential_scope"] = args.credential_scope
	decision = qpm.reserve(request=request)
	emit("reserve", backend=args.backend, request=request, decision=decision)
	if decision.get("status") != "accepted" or not decision.get(
			"reservation_id"):
		raise DEFwError(f"reservation was not accepted: {decision}")
	return 0


def release(args):
	qpm = resolve_qpm(args.backend, args.timeout)
	reservation_id = normalize_reservation_id(args.reservation_id)
	result = qpm.release(reservation_id=reservation_id, reason=args.reason)
	emit(
		"release",
		backend=args.backend,
		reservation_id=reservation_id,
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
	reserve_parser.add_argument("--depth", type=int, default=1)
	reserve_parser.add_argument("--one-q-gates", type=int, default=0)
	reserve_parser.add_argument("--two-q-gates", type=int, default=0)
	reserve_parser.add_argument("--measurements", type=int)
	reserve_parser.add_argument("--workload-kind", default="quantum")
	reserve_parser.add_argument("--operation", default="async_run")
	reserve_parser.add_argument("--walltime", type=int, default=300)
	reserve_parser.add_argument("--ttl", type=int, default=600)
	reserve_parser.add_argument("--timeout", type=float, default=40.0)
	reserve_parser.add_argument("--target-device")
	reserve_parser.add_argument("--scope-id")
	reserve_parser.add_argument("--owner")
	reserve_parser.add_argument("--job-id")
	reserve_parser.add_argument("--allocation-id")
	reserve_parser.add_argument("--parameters-json")
	reserve_parser.add_argument("--workload-json")
	reserve_parser.add_argument("--run-context-json")
	reserve_parser.add_argument("--task-class-json")
	reserve_parser.add_argument("--analytics-json")
	reserve_parser.add_argument("--credential-hint")
	reserve_parser.add_argument("--credential-hint-json")
	reserve_parser.add_argument("--credential-handle")
	reserve_parser.add_argument("--credential-scope")
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
