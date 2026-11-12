import argparse
import json
import os
import sys

SCHEMA = "qfw-target-v1"
RECORDS = (
	("backend_info", "get_backend_info"),
	("device_info", "get_device_info"),
	("coupling_graph", "get_coupling_graph"),
	("calibration", "get_calibration_snapshot"),
)


def target_document(resolved, telemetry, lib=None):
	settings = dict(resolved.properties or {})
	document = {
		"schema": SCHEMA,
		"service_id": resolved.service_id,
		"provider": settings.get("provider"),
		"settings": settings,
	}
	errors = {}
	for key, call in RECORDS:
		try:
			document[key] = getattr(telemetry, call)(lib=lib)
		except Exception as exc:
			document[key] = None
			errors[key] = error_message(exc)
	if errors:
		document["errors"] = errors
	return document


def error_message(exc):
	return " ".join(str(getattr(exc, "msg", None) or exc).split())


def target_from_json(document):
	from qfw_qiskit.qfw_target import build_device_target
	if isinstance(document, (str, bytes)):
		document = json.loads(document)
	if document.get("schema") != SCHEMA:
		raise ValueError(
			f"not a {SCHEMA} document: schema {document.get('schema')!r}")
	target = build_device_target(
		document.get("backend_info"), document.get("device_info"),
		document.get("coupling_graph"), document.get("calibration"))
	if target is not None:
		return target
	return generic_target(document.get("settings") or {})


def generic_target(settings):
	from qfw_qiskit.qfw_target import QFW_NUM_QUBITS, build_qfw_target
	return build_qfw_target(QFW_NUM_QUBITS, settings.get("instructions"))


def query(provider=None, service_id=None, lib=None, timeout=10):
	from defw_app_util import defw_get_directory_service
	from qfw_qiskit.qfw_job import SHIM_LIB_ENV, normalize_shim_library
	from qfw_qiskit.qfw_lookup_service import TELEMETRY_BINDING, bind_qpm
	from qfw_qiskit.qfw_telemetry import QFwTelemetry
	from qfw_qiskit.qpm_selection import qpm_selection_for_provider

	selection = qpm_selection_for_provider(provider) if provider else {}
	resolved, lifecycle_binding, _reservation_id = bind_qpm(
		defw_get_directory_service(timeout=timeout),
		selection.get("qpm_type", -1), selection.get("qpm_capabilities", -1),
		timeout=timeout, provider=selection.get("provider"),
		service_id=service_id)
	properties = dict(resolved.properties or {})
	if lib is None and properties.get("provider") == "shim":
		lib = os.environ.get(SHIM_LIB_ENV)
	api = lifecycle_binding.api(
		TELEMETRY_BINDING, expected_runtime_id=resolved.runtime_id)
	return target_document(
		resolved, QFwTelemetry(api),
		lib=normalize_shim_library(lib, properties))


def main(argv=None):
	parser = argparse.ArgumentParser(
		prog="qfw-target",
		description="Print what a QPM says about its device, as one "
			    f"{SCHEMA} JSON document, without reserving it.")
	parser.add_argument("--run-dir", help="the QFw run to query")
	parser.add_argument("--provider")
	parser.add_argument("--service-id")
	parser.add_argument("--lib", help="the shim library to describe")
	parser.add_argument(
		"--timeout", type=int, default=10,
		help="seconds to wait for the directory and for a matching QPM")
	parser.add_argument("--output", help="write here instead of stdout")
	args = parser.parse_args(argv)
	try:
		document = query(
			args.provider, args.service_id, args.lib, args.timeout)
	except Exception as exc:
		print(f"qfw-target: {error_message(exc)}", file=sys.stderr)
		return 1
	text = json.dumps(document, indent=2, sort_keys=True, default=str)
	if args.output:
		with open(args.output, "w", encoding="utf-8") as stream:
			stream.write(text + "\n")
	else:
		print(text)
	return 0


if __name__ == "__main__":
	sys.exit(main())
