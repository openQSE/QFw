#!/usr/bin/env python3

import argparse
import os
import select
import sys
import traceback
from time import sleep, time

import yaml
from api_qpm import QPM, QPMCapability, QPMType
import defw
from defw import me
from defw_app_util import defw_get_directory_service
from defw_event_baseapi import BaseEventAPI
from defw_exception import DEFwError, DEFwNotReady
from defw_util import fg, prformat
from qfw_example_report import emit_result

EVENT_TYPE_CIRC_RESULT = 1
DEFAULT_CALL_SEQUENCE = (
	"test",
	"get_backend_info",
	"get_device_info",
	"get_coupling_graph",
	"get_calibration_snapshot",
	"async_run",
	"get_last_job_metadata",
)
# The composable device-introspection facet. Each of these can be routed to a
# specific shim library from the client (lib=...), so --libs fans them out
# across the requested libraries in order for a side-by-side comparison.
INTROSPECTION_CALLS = (
	"get_backend_info",
	"get_device_info",
	"get_coupling_graph",
	"get_calibration_snapshot",
)


def exposed_qpm_api_names():
	return {
		name for name, value in vars(QPM).items()
		if callable(value) and not name.startswith("_")
	}


def validate_call_name(call):
	if call is None:
		return None
	api_names = exposed_qpm_api_names()
	if call not in api_names:
		valid = sorted(api_names)
		raise SystemExit(
			f"unsupported --call {call!r}. Expected api_qpm QPM API "
			f"name. Valid APIs: {', '.join(valid)}")
	return call


def parse_lib_list(value):
	# Parse an ordered, comma-separated preference list of shim libraries
	# (e.g. "qdmi,qrmi"), preserving order and dropping duplicates.
	libs = []
	for item in (value or "").split(","):
		item = item.strip().lower()
		if not item:
			continue
		if item not in ("qrmi", "qdmi"):
			raise argparse.ArgumentTypeError(
				f"--libs entries must be qrmi or qdmi, got {item!r}")
		if item not in libs:
			libs.append(item)
	return libs


def parse_args():
	parser = argparse.ArgumentParser(
		description="Run the QRMI/QDMI shim QPM smoke test over DEFw RPC.")
	parser.add_argument(
		"--lib", choices=("qrmi", "qdmi", "default"), default="default",
		help="Optional shim library override for test calls.")
	parser.add_argument(
		"--libs", type=parse_lib_list, default=[], metavar="LIB[,LIB]",
		help="Ordered preference list of shim libraries to run each "
		     "introspection call through (e.g. 'qdmi,qrmi'), for a "
		     "side-by-side comparison. Overrides --lib for introspection "
		     "calls; a library that does not serve a call is skipped.")
	parser.add_argument(
		"--call", default=None,
		help="Run only one server-side QPM API by name.")
	parser.add_argument(
		"--device-id", default="ornl-iqm-20q",
		help="Shim QPM device_id property to select.")
	parser.add_argument(
		"--system-up-timeout", type=int, default=40,
		help="Seconds to wait for the shim QPM service.")
	parser.add_argument(
		"--circuit-run-timeout", type=int, default=100,
		help="Seconds to wait for async circuit completion.")
	parser.add_argument(
		"--shots", type=int, default=100,
		help="Shots for the run_circuit smoke request.")
	return parser.parse_args()


def requested_lib(args):
	if args.lib == "default":
		return None
	return args.lib


def introspection_libs(args):
	# Ordered libraries to run each introspection call through. The client-driven
	# --libs preference list wins; otherwise fall back to the single --lib
	# selection ([None] means let the descriptor's preference route it).
	if args.libs:
		return list(args.libs)
	return [requested_lib(args)]


def introspection_api(call, qpm):
	return {
		"get_backend_info": qpm.get_backend_info,
		"get_device_info": qpm.get_device_info,
		"get_coupling_graph": qpm.get_coupling_graph,
		"get_calibration_snapshot": qpm.get_calibration_snapshot,
	}[call]


def fetch_capability_map(qpm):
	# Best-effort per-resource gap map {call: [libs]}, used only to skip an
	# introspection call a library does not serve during a --libs comparison.
	# If unavailable, the fan-out simply attempts every requested library.
	try:
		cap_map = qpm.capability_map()
		dump_result("capability_map", cap_map)
		return cap_map
	except Exception as exc:
		prformat(
			fg.red + fg.bold,
			f"[shim-smoke] capability_map unavailable: {exc}")
		return {}


def run_introspection(call, qpm, libs, cap_map, failures):
	# Run one introspection call once per requested library, in order, so the
	# results can be compared back-to-back (e.g. QDMI then QRMI). In fan-out
	# mode (more than one library) a library that does not serve the call is
	# skipped with a note rather than recorded as a failure.
	func = introspection_api(call, qpm)
	fanout = len(libs) > 1
	served = cap_map.get(call) if cap_map else None
	for lib in libs:
		if fanout and lib and served is not None and lib not in served:
			print(f"[shim-smoke] {call}[{lib}]: SKIPPED "
			      f"(library does not serve {call})")
			continue
		label = f"{call}[{lib}]" if lib else call
		kwargs = {"lib": lib} if lib else {}
		call_api(label, func, failures, **kwargs)


def _binding_properties(binding):
	service_record = binding.get("service_record", binding)
	return dict(service_record.get("properties") or {})


def reserve_shim_qpm(device_id, timeout):
	dirsvc = defw_get_directory_service()
	qpm_type = QPMType.QPM_TYPE_HARDWARE
	qpm_capability = QPMCapability.QPM_CAP_SUPERCONDUCTING
	start = time()

	while time() - start < timeout:
		bindings = dirsvc.resolve_services(
			service_name="QPM",
			qpm_type=qpm_type,
			qpm_capabilities=qpm_capability,
			properties={"provider": "shim"},
		)
		matches = []
		for binding in bindings:
			props = _binding_properties(binding)
			if device_id and props.get("device_id") != device_id:
				continue
			matches.append(binding)

		if matches:
			prformat(
				fg.green + fg.bold,
				f"selected shim QPM: {_binding_properties(matches[0])}")
			return defw.connect_to_binding(matches[0])

		sleep(1)

	raise DEFwError(
		f"failed to find shim QPM for device_id={device_id!r} "
		f"within {timeout} seconds")


def wait_ready(qpm, timeout):
	start = time()
	while time() - start < timeout:
		try:
			qpm.is_ready()
			return
		except Exception as exc:
			if isinstance(exc, DEFwNotReady):
				sleep(1)
				continue
			raise
	raise DEFwError(f"shim QPM was not ready within {timeout} seconds")


def dump_result(label, result):
	print(f"\n[shim-smoke] {label}:")
	print(yaml.safe_dump(result, sort_keys=False))


def call_api(label, func, failures, **kwargs):
	try:
		result = func(**kwargs)
		dump_result(label, result)
		return result
	except Exception as exc:
		if can_skip_qrmi_device_access(kwargs.get("lib"), exc):
			print(f"[shim-smoke] {label}: SKIPPED "
			      "(QRMI device access is not configured)")
			return None
		failures.append((label, exc))
		prformat(fg.red + fg.bold, f"[shim-smoke] {label}: FAILED: {exc}")
		traceback.print_exc()
		return None


def smoke_qasm():
	return """OPENQASM 2.0;
include "qelib1.inc";
qreg q[1];
creg c[1];
x q[0];
measure q[0] -> c[0];
"""


def event_payload(event):
	if hasattr(event, "get_event"):
		return event.get_event()
	return event


def wait_for_async_result(event_api, expected_cid, timeout):
	start = time()
	event_fd = event_api.fileno()
	while time() - start < timeout:
		readable, _, _ = select.select([event_fd], [], [], 1)
		if not readable:
			continue
		events = event_api.get()
		if not isinstance(events, list):
			events = [events]
		for event in events:
			payload = event_payload(event)
			if payload.get("cid") == expected_cid:
				return payload
	raise DEFwError(
		f"timed out waiting for circuit result cid={expected_cid!r}")


def require_provider_completion():
	value = os.environ.get("QFW_SHIM_SMOKE_REQUIRE_PROVIDER", "")
	return value.lower() in ("1", "true", "yes", "on")


def live_qrmi_token_present():
	token = os.environ.get("QFW_API_KEY")
	if token and token != "dummy-api-key":
		return True
	for name, value in os.environ.items():
		if name.endswith("_QRMI_IQM_ISA_TOKEN") and value and (
				value != "dummy-api-key"):
			return True
	return False


def missing_device_access_error(exc):
	text = str(exc)
	return (
		"QPU credential DB was not found" in text or
		"QRMI driver could not resolve IQM device access" in text or
		"set QFW_QC_URL/QFW_API_KEY" in text)


def can_skip_qrmi_device_access(lib, exc):
	if require_provider_completion() or live_qrmi_token_present():
		return False
	if lib not in (None, "qrmi"):
		return False
	return missing_device_access_error(exc)


def can_skip_qrmi_provider_completion(qpm, lib):
	if require_provider_completion() or live_qrmi_token_present():
		return False
	if lib not in (None, "qrmi"):
		return False
	try:
		backend_info = qpm.get_backend_info(lib="qrmi")
	except Exception as exc:
		if can_skip_qrmi_device_access("qrmi", exc):
			print("[shim-smoke] run_circuit: SKIPPED provider completion "
			      "(QRMI device access is not configured)")
			return True
		prformat(
			fg.red + fg.bold,
			f"[shim-smoke] QRMI provider preflight unavailable: {exc}")
		return False
	active_qubits = backend_info.get("active_qubits") or []
	if active_qubits:
		return False
	print("[shim-smoke] run_circuit: SKIPPED provider completion "
	      "(QRMI target has no active qubits and no live token is present)")
	return True


def admission_policy_path():
	candidates = [
		os.environ.get("QFW_SHIM_SMOKE_ADMISSION_POLICY_PATH"),
		os.environ.get("QHW_ADMISSION_POLICY_PATH"),
	]
	qfw_prefix = os.environ.get("QFW_PREFIX")
	if qfw_prefix:
		base_dir = os.path.dirname(qfw_prefix)
		candidates.extend([
			os.path.join(base_dir, "qhw-admission", "build", "policies"),
			os.path.join(
				base_dir, "install", "qhw-admission", "lib",
				"qhw_admission", "policies"),
		])
	for candidate in candidates:
		if candidate and os.path.isdir(candidate):
			return candidate
	return None


def configure_admission_policy(qpm):
	policy = {"name": "unlimited"}
	policy_path = admission_policy_path()
	if policy_path:
		policy["path"] = policy_path
	result = qpm.set_admission_policy(policy)
	dump_result("set_admission_policy", result)


def reserve_execution(qpm, args):
	configure_admission_policy(qpm)
	job_id = os.environ.get("SLURM_JOB_ID", "shim-smoke")
	request = {
		"owner": {"user": os.environ.get("USER", "shim-smoke")},
		"job_id": job_id,
		"allocation_id": job_id,
		"target_device_id": args.device_id,
		"num_qubits": 1,
		"walltime_ns": 2_000_000_000,
		"ttl_ns": 60_000_000_000,
		"workload": {
			"example": "qfw_shim_smoke",
			"operation": "async_run",
		},
		"run_context": {"operation": "async_run"},
		"task_class": {
			"count": 1,
			"qubit_count": 1,
			"shots": args.shots,
			"measurement_count": 1,
		},
	}
	decision = qpm.reserve(request)
	dump_result("reserve", decision)
	if decision.get("status") != "accepted" or not decision.get(
			"reservation_id"):
		raise DEFwError(f"reservation was not accepted: {decision}")
	return decision["reservation_id"]


def run_circuit(qpm, lib, shots, timeout, reservation_id):
	event_api = BaseEventAPI()
	event_api.register_external()
	qpm.register_event_notification(
		me.my_endpoint(), EVENT_TYPE_CIRC_RESULT, event_api.class_id())

	info = {
		"qasm": smoke_qasm(),
		"num_qubits": 1,
		"num_shots": shots,
		"compiler": "staq",
	}
	if lib:
		info["lib"] = lib

	response = qpm.async_run(info, reservation_id)
	dump_result("async_run", response)
	cid = response.get("cid") if isinstance(response, dict) else response
	if not cid:
		raise DEFwError(f"async_run did not return a circuit id: {response}")
	result = wait_for_async_result(event_api, cid, timeout)
	dump_result("run_circuit", result)
	if result.get("rc") != 0:
		if can_skip_qrmi_provider_completion(qpm, lib):
			return cid, result, False
		raise DEFwError(f"run_circuit failed for cid={cid}: {result}")
	return cid, result, True


def run_named_call(call, qpm, libs, cap_map, failures, args,
		   cid=None, reservation_id=None):
	if call == "test":
		call_api("test", qpm.test, failures)
		return cid
	if call in INTROSPECTION_CALLS:
		run_introspection(call, qpm, libs, cap_map, failures)
		return cid
	if call == "get_last_job_metadata":
		# Execution-facet call: stays with the single --lib selection (bound to
		# the execution owner), not the introspection preference list.
		if cid is None and args.call != "get_last_job_metadata":
			print("[shim-smoke] get_last_job_metadata: SKIPPED "
			      "(async_run did not complete on the provider)")
			return cid
		exec_lib = requested_lib(args)
		kwargs = {"lib": exec_lib} if exec_lib else {}
		call_api(
			"get_last_job_metadata", qpm.get_last_job_metadata,
			failures, cid=cid, **kwargs)
		return cid

	if call == "async_run":
		try:
			cid, _, completed = run_circuit(
				qpm, requested_lib(args), args.shots,
				args.circuit_run_timeout, reservation_id)
			if not completed:
				cid = None
		except Exception as exc:
			if can_skip_qrmi_device_access(requested_lib(args), exc):
				print("[shim-smoke] async_run: SKIPPED "
				      "(QRMI device access is not configured)")
				return None
			failures.append(("async_run", exc))
			prformat(
				fg.red + fg.bold,
				f"[shim-smoke] async_run: FAILED: {exc}")
			traceback.print_exc()
		return cid

	raise DEFwError(
		f"{call!r} is an api_qpm API, but this smoke test does not have "
		"a fixture for it")


def summarize_failures(failures):
	if not failures:
		prformat(fg.green + fg.bold, "SHIM REMOTE QPM SMOKE: PASS")
		return 0

	prformat(fg.red + fg.bold, "SHIM REMOTE QPM SMOKE: FAIL")
	for label, exc in failures:
		prformat(fg.red + fg.bold, f"  {label}: {type(exc).__name__}: {exc}")
	return 1


def main():
	args = parse_args()
	args.call = validate_call_name(args.call)
	libs = introspection_libs(args)
	failures = []

	qpm = reserve_shim_qpm(args.device_id, args.system_up_timeout)
	reservation_id = None
	try:
		wait_ready(qpm, args.system_up_timeout)
		# Only needed to skip unsupported libraries during a --libs comparison.
		cap_map = fetch_capability_map(qpm) if len(libs) > 1 else {}
		cid = None
		calls = (args.call,) if args.call else DEFAULT_CALL_SEQUENCE
		if "async_run" in calls:
			reservation_id = reserve_execution(qpm, args)
		for call in calls:
			cid = run_named_call(
				call, qpm, libs, cap_map, failures, args, cid,
				reservation_id=reservation_id)
	finally:
		if reservation_id is not None:
			try:
				dump_result(
					"release", qpm.release(reservation_id, reason=0))
			except Exception as exc:
				failures.append(("release", exc))
				prformat(
					fg.red + fg.bold,
					f"[shim-smoke] release: FAILED: {exc}")
				traceback.print_exc()
		try:
			qpm.shutdown()
		except Exception:
			pass

	rc = summarize_failures(failures)
	emit_result(
		"shim-smoke",
		status="ok" if rc == 0 else "error",
		parameters={
			"lib": args.lib,
			"libs": libs,
			"call": args.call,
			"device_id": args.device_id,
			"shots": args.shots,
		},
		metrics={
			"failed_call_count": len(failures),
			"failed_calls": [
				{"label": label, "error": f"{type(exc).__name__}: {exc}"}
				for label, exc in failures
			],
		},
	)
	return rc


if __name__ == "__main__":
	rc = 1
	try:
		rc = main()
	except Exception:
		traceback.print_exc()
	finally:
		try:
			me.exit()
		except SystemExit:
			pass
	sys.exit(rc)
