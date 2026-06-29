#!/usr/bin/env python3

import argparse
import select
import sys
import traceback
from time import sleep, time

import yaml
from api_qpm import QPM, QPMCapability, QPMType
import defw
from defw import me
from defw_app_util import defw_get_resource_mgr
from defw_event_baseapi import BaseEventAPI
from defw_exception import DEFwError, DEFwNotReady
from defw_util import fg, prformat

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


def parse_args():
	parser = argparse.ArgumentParser(
		description="Run the QRMI/QDMI shim QPM smoke test over DEFw RPC.")
	parser.add_argument(
		"--lib", choices=("qrmi", "qdmi", "default"), default="default",
		help="Optional shim library override for test calls.")
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


def reserve_shim_qpm(device_id, timeout):
	rmgr = defw_get_resource_mgr()
	svc_type = QPMType.QPM_TYPE_IQM | QPMType.QPM_TYPE_HARDWARE
	svc_cap = QPMCapability.QPM_CAP_SUPERCONDUCTING
	start = time()

	while time() - start < timeout:
		infos = rmgr.get_services("QPM", svc_type, svc_cap)
		matches = []
		for info in infos:
			props = info.get_properties()
			if props.get("provider") != "shim":
				continue
			if device_id and props.get("device_id") != device_id:
				continue
			matches.append(info)

		if matches:
			prformat(
				fg.green + fg.bold,
				f"selected shim QPM: {matches[0].get_properties()}")
			return defw.connect_to_resource(matches, "QPM")[0]

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


def run_circuit(qpm, lib, shots, timeout):
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

	cid = qpm.async_run(info)
	result = wait_for_async_result(event_api, cid, timeout)
	dump_result("run_circuit", result)
	if result.get("rc") != 0:
		raise DEFwError(f"run_circuit failed for cid={cid}: {result}")
	return cid, result


def run_named_call(call, qpm, lib_kwargs, failures, args, cid=None):
	if call == "test":
		call_api("test", qpm.test, failures)
		return cid
	if call == "get_backend_info":
		call_api("get_backend_info", qpm.get_backend_info, failures,
		         **lib_kwargs)
		return cid
	if call == "get_device_info":
		call_api("get_device_info", qpm.get_device_info, failures,
		         **lib_kwargs)
		return cid
	if call == "get_coupling_graph":
		call_api("get_coupling_graph", qpm.get_coupling_graph, failures,
		         **lib_kwargs)
		return cid
	if call == "get_calibration_snapshot":
		call_api(
			"get_calibration_snapshot", qpm.get_calibration_snapshot,
			failures, **lib_kwargs)
		return cid
	if call == "get_last_job_metadata":
		call_api(
			"get_last_job_metadata", qpm.get_last_job_metadata,
			failures, cid=cid, **lib_kwargs)
		return cid
	if call == "async_run":
		try:
			cid, _ = run_circuit(
				qpm, requested_lib(args), args.shots,
				args.circuit_run_timeout)
		except Exception as exc:
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
	lib = requested_lib(args)
	lib_kwargs = {"lib": lib} if lib else {}
	failures = []

	qpm = reserve_shim_qpm(args.device_id, args.system_up_timeout)
	try:
		wait_ready(qpm, args.system_up_timeout)
		cid = None
		calls = (args.call,) if args.call else DEFAULT_CALL_SEQUENCE
		for call in calls:
			cid = run_named_call(call, qpm, lib_kwargs, failures, args, cid)
	finally:
		try:
			qpm.shutdown()
		except Exception:
			pass

	return summarize_failures(failures)


if __name__ == "__main__":
	rc = 1
	try:
		rc = main()
	except Exception:
		traceback.print_exc()
	finally:
		me.exit()
	sys.exit(rc)
