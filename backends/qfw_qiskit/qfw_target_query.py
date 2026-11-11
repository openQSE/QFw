import argparse
import json
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
			errors[key] = str(exc)
	if errors:
		document["errors"] = errors
	return document


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
	return generic_target_at_published_width(document.get("settings") or {})


def generic_target_at_published_width(settings):
	from qfw_qiskit.qfw_target import QFW_NUM_QUBITS, build_qfw_target
	return build_qfw_target(
		int(settings.get("num_qubits") or QFW_NUM_QUBITS),
		settings.get("instructions"))


def query(provider=None, service_id=None, lib=None, timeout=10):
	import defw
	from defw_app_util import defw_get_directory_service
	from qfw_qiskit.qfw_lookup_service import (
		EXECUTION_BINDING, TELEMETRY_BINDING)
	from qfw_qiskit.qfw_telemetry import QFwTelemetry
	from qfw_qiskit.qpm_resolver import QPMResolver

	resolver = QPMResolver.from_environment(
		dirsvc=defw_get_directory_service(timeout=timeout),
		defw_module=defw)
	request = {"timeout": timeout, "binding_name": EXECUTION_BINDING}
	if provider:
		request["provider"] = provider
	if service_id:
		request["service_id"] = service_id
	resolved = resolver.resolve(**request)
	api = resolver.managed_binding(resolved).api(
		TELEMETRY_BINDING, expected_runtime_id=resolved.runtime_id)
	return target_document(resolved, QFwTelemetry(api), lib=lib)


def main(argv=None):
	parser = argparse.ArgumentParser(
		prog="qfw-target",
		description="Print what a QPM says about its device, as one "
			    f"{SCHEMA} JSON document, without reserving it.")
	parser.add_argument("--provider")
	parser.add_argument("--service-id")
	parser.add_argument("--lib", help="the shim library to describe")
	parser.add_argument("--timeout", type=int, default=10)
	parser.add_argument("--output", help="write here instead of stdout")
	args = parser.parse_args(argv)
	document = query(args.provider, args.service_id, args.lib, args.timeout)
	text = json.dumps(document, indent=2, sort_keys=True, default=str)
	if args.output:
		with open(args.output, "w", encoding="utf-8") as stream:
			stream.write(text + "\n")
	else:
		print(text)
	return 0


if __name__ == "__main__":
	sys.exit(main())
