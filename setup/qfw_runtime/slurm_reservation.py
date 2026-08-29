import argparse
import sys

from defw_app_util import defw_get_directory_service
from defw_exception import DEFwError
from qfw_qiskit.qpm_resolver import QPMResolver
from qfw_qiskit.reservation_set import (
	QPMReservation,
	encode_qfw_reservations,
	parse_qfw_reservations,
)


OUTPUT_PREFIX = "QFW_RESERVATIONS="


def _resolver():
	dirsvc = defw_get_directory_service()
	return QPMResolver.from_environment(dirsvc=dirsvc)


def _admission_client(resolver, service_id, timeout):
	return resolver.connect_reserved(
		service_id,
		"1",
		timeout=timeout,
		binding_name="admission",
	).client


def reserve(args, resolver=None):
	if len(set(args.service_id)) != len(args.service_id):
		raise DEFwError("a Slurm job may reserve each QPM service only once")
	resolver = resolver or _resolver()
	reservations = []
	try:
		for service_id in args.service_id:
			qpm = _admission_client(resolver, service_id, args.timeout)
			request = {
				"owner": {"user": args.owner},
				"job_id": args.job_id,
				"allocation_id": args.allocation_id,
				"scope_id": args.allocation_id,
				"walltime_ns": max(1, args.walltime_seconds) * 1_000_000_000,
				"ttl_ns": max(1, args.ttl_seconds) * 1_000_000_000,
				"workload_kind": "slurm",
				"workload": {"scheduler": "slurm"},
				"task_class": {
					"count": 1,
					"qubit_count": 1,
					"depth": 1,
					"shots": 1,
					"measurement_count": 1,
				},
			}
			decision = qpm.reserve(request=request)
			reservation_id = decision.get("reservation_id")
			if (decision.get("status") != "accepted" or
					reservation_id in (None, 0, "0")):
				raise DEFwError(
					f"QPM {service_id!r} rejected reservation: {decision}")
			reservations.append(QPMReservation(
				service_id, str(reservation_id)))
	except Exception:
		_release_reservations(resolver, reservations, args.timeout)
		raise
	encoded = encode_qfw_reservations(reservations)
	print(f"{OUTPUT_PREFIX}{encoded}", flush=True)
	return encoded


def release(args, resolver=None):
	resolver = resolver or _resolver()
	reservations = parse_qfw_reservations(args.reservations)
	errors = _release_reservations(resolver, reservations, args.timeout)
	if errors:
		raise DEFwError("; ".join(errors))
	return 0


def _release_reservations(resolver, reservations, timeout):
	errors = []
	for reservation in reversed(list(reservations)):
		try:
			qpm = _admission_client(
				resolver, reservation.service_id, timeout)
			result = qpm.release(
				reservation_id=reservation.reservation_id,
				reason=0)
			if result.get("status") != "accepted":
				raise DEFwError(str(result))
		except Exception as exc:
			errors.append(
				f"QPM {reservation.service_id!r} release failed: {exc}")
	return errors


def build_parser():
	parser = argparse.ArgumentParser(prog="qfw-slurm-reservation")
	subparsers = parser.add_subparsers(dest="command", required=True)

	reserve_parser = subparsers.add_parser("reserve")
	reserve_parser.add_argument(
		"--service-id", action="append", required=True)
	reserve_parser.add_argument("--owner", required=True)
	reserve_parser.add_argument("--job-id", required=True)
	reserve_parser.add_argument("--allocation-id", required=True)
	reserve_parser.add_argument("--walltime-seconds", type=int, default=300)
	reserve_parser.add_argument("--ttl-seconds", type=int, default=600)
	reserve_parser.add_argument("--timeout", type=float, default=40.0)
	reserve_parser.set_defaults(func=reserve)

	release_parser = subparsers.add_parser("release")
	release_parser.add_argument("--reservations", required=True)
	release_parser.add_argument("--timeout", type=float, default=40.0)
	release_parser.set_defaults(func=release)
	return parser


def main(argv=None):
	args = build_parser().parse_args(argv)
	try:
		result = args.func(args)
		return result if isinstance(result, int) else 0
	except Exception as exc:
		print(f"qfw-slurm-reservation: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())
