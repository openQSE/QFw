"""Shared explicit backend selector for QFw examples."""

import argparse
from importlib import metadata
import sys


BACKEND_API_CHOICES = ("legacy", "v2")


def add_backend_api_argument(parser):
	parser.add_argument(
		"--backend-api",
		choices=BACKEND_API_CHOICES,
		default="legacy",
		help="select QFwBackend or QFwBackendV2 without fallback",
	)
	return parser


def backend_api_from_argv(argv=None):
	values = sys.argv[1:] if argv is None else list(argv)
	parser = argparse.ArgumentParser(add_help=False)
	add_backend_api_argument(parser)
	args, remaining = parser.parse_known_args(values)
	if argv is None:
		sys.argv[:] = [sys.argv[0], *remaining]
	return args.backend_api


def create_qfw_backend(provider, backend_api):
	if backend_api == "legacy":
		from qfw_qiskit import QFwBackend
		backend = QFwBackend(provider=provider)
	elif backend_api == "v2":
		from qfw_qiskit import QFwBackendV2
		backend = QFwBackendV2()
	else:
		raise ValueError(f"unknown backend API: {backend_api}")
	print({
		"backend_api": backend_api,
		"backend_class": f"{type(backend).__module__}.{type(backend).__name__}",
		"qiskit_version": metadata.version("qiskit"),
		"qfw_qiskit_version": _distribution_version("qfw-qiskit"),
	})
	return backend


def _distribution_version(name):
	try:
		return metadata.version(name)
	except metadata.PackageNotFoundError:
		return "source-install"


def qfw_run_options(backend_api):
	if backend_api == "v2":
		return {}
	from qfw_example_context import qfw_reservation_options
	return qfw_reservation_options()
