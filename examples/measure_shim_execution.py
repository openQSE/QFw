#!/usr/bin/env python
"""Measure where time goes executing a circuit: QRMI vs QDMI vs native.

USES QPU TIME. Unlike measure_shim_introspection.py this submits real circuits
to the device, one per path per repeat. Defaults are deliberately small: a
single-qubit circuit at 10 shots, one repeat.

Three paths are compared: the two shim libraries, and QFw's native IQM service
client (svc_iqm_qpm), which talks to iqm-client directly. The native arm is the
baseline the interface-convergence question needs -- it shows what the shim
layers cost, and what they save.

The question is whether the envelope-assembly difference between the two
libraries costs anything measurable. QRMI's caller builds the entire IQM run
request -- circuits, shots, calibration set -- and submits it as one opaque
provider document. QDMI's caller submits a single circuit and the device
implementation assembles the surrounding request itself, with shots passed as a
typed job parameter. So the same execution is split differently between caller
and library, and this measures that split.

Phases per run:

  prep      client-side preparation before the provider is contacted:
            transcoding OpenQASM to an IQM circuit, and building the payload
            (a RunRequest for QRMI, a serialized single circuit for QDMI).
            Derived, since the drivers begin their own clock at submit.
  submit    handing the job to the provider
  wait      polling until the job reaches a terminal state
  fetch     retrieving the results
  total     measured around the whole run_circuit call

Connections opened during the run are counted as well. That matters here for
the same reason it did for introspection: a client that reconnects per request
pays a TLS handshake every time, and execution polls repeatedly, so a
per-request reconnect is multiplied by the poll count rather than paid once.

The drivers are opened and warmed before timing, so this measures execution and
not session setup -- see measure_shim_introspection.py for that.

Run it inside the container, with device access configured:

  docker exec c5 bash -c 'source /opt/qfw/qhpc/QFw/setup/qfw_activate \\
      >/dev/null 2>&1 && source "$QFW_IMAGE_VENV/bin/activate" \\
      && python -u /opt/qfw/qhpc/QFw/examples/measure_shim_execution.py'
"""

import argparse
import json
import os
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from measurement_support import (  # noqa: E402
	ConnectionSampler, MeasurementError, driver_for, endpoint_context,
	endpoint_port, fmt_ms, open_handle, require_counts, require_qubits)

# One qubit, flipped and measured: the smallest circuit that still exercises
# transcoding, submission, and a non-trivial result. Expect counts to be all
# "1" apart from readout error.
SMOKE_QASM = """OPENQASM 2.0;
include "qelib1.inc";
qreg q[1];
creg c[1];
x q[0];
measure q[0] -> c[0];
"""


class _Circuit:
	"""Minimal duck-typed circuit: what the drivers read off a QFw job.

	Both drivers take `circuit.info["qasm"]` plus execution options, and an
	optional get_cid(). Constructing one here keeps DEFw RPC out of the
	measurement, the same way the introspection script bypasses the service.
	"""

	def __init__(self, qasm, shots, timeout, poll_interval):
		self._cid = str(uuid.uuid4())
		self.info = {
			"qasm": qasm,
			"num_shots": int(shots),
			"timeout": float(timeout),
			"poll_interval": float(poll_interval),
			"cid": self._cid,
		}

	def get_cid(self):
		return self._cid


def _warm(library, driver):
	"""Open the handle and pay every first-call cost before timing starts.

	Two things are warmed. The driver's own handle and cache, so a run is not
	charged for session setup. And the transcoder, because build_iqm_circuit()
	imports Qiskit and iqm.qiskit_iqm lazily on first use: without this the
	first library measured is charged roughly 3.4 seconds of module import and
	appears to have ~57x the payload-assembly cost of the second. That is an
	ordering artifact, confirmed by reversing the library order and watching
	the penalty follow position rather than library.
	"""
	open_handle(library, driver)

	device_info = driver.get_device_info()
	require_qubits(device_info)

	from util.iqm_transcode import build_iqm_circuit
	qubits = [q["id"] for q in device_info.get("qubits", []) if q.get("id")]
	# Same call the drivers make, against the same dynamic-architecture shape.
	build_iqm_circuit(SMOKE_QASM, {"qubits": qubits}, None)


def measure_library(library, device_id, args, count_port=None):
	from svc_lib_qpm.descriptor import resolve_descriptor

	descriptor = resolve_descriptor(device_id)
	driver = driver_for(library, descriptor)

	warm_start = time.perf_counter()
	_warm(library, driver)
	warm_seconds = time.perf_counter() - warm_start

	runs = []
	for _ in range(args.repeat):
		circuit = _Circuit(SMOKE_QASM, args.shots, args.circuit_timeout,
				args.poll_interval)
		with ConnectionSampler(count_port,
				enabled=count_port is not None) as conns:
			started = time.perf_counter()
			record = driver.run_circuit(circuit)
			total = time.perf_counter() - started

		counts = require_counts(record)

		# The drivers time submit/wait/fetch from their own clock, started just
		# before submission. Anything before that -- transcode and payload
		# assembly -- is the difference against the total measured here.
		driver_timing = (driver.get_last_job_timing() or {}).get("timing") or {}
		submit = driver_timing.get("submit_seconds")
		wait = driver_timing.get("wait_seconds")
		fetch = driver_timing.get("result_fetch_seconds")
		accounted = sum(v for v in (submit, wait, fetch) if v is not None)

		runs.append({
			"total_seconds": total,
			"prep_seconds": max(total - accounted, 0.0),
			"submit_seconds": submit,
			"wait_seconds": wait,
			"result_fetch_seconds": fetch,
			"driver_total_wall_seconds": driver_timing.get(
					"total_wall_seconds"),
			"connections": len(conns.endpoints) if conns.supported else None,
			# Provider-reported phase durations, where the path exposes any.
			# Only the native client does: it reads the IQM job timeline, so it
			# can separate queue wait from execution. Neither QRMI nor QDMI
			# passes that through, which is why this is None for them.
			"provider_durations": (driver.provider_durations()
					if hasattr(driver, "provider_durations") else None),
			"counts": counts,
			"shots": args.shots,
		})

	return {
		"library": library,
		"device_id": device_id,
		"warm_seconds": warm_seconds,
		"runs": runs,
	}


def _median(runs, key):
	values = [r[key] for r in runs if r.get(key) is not None]
	return statistics.median(values) if values else None


def render(record):
	context = record.get("context", {})
	print("QFw execution cost -- QRMI vs QDMI vs native IQM client")
	print(f"  endpoint      {context.get('base_url', 'unknown')}")
	print(f"  host          {record.get('host', 'unknown')}")
	print(f"  circuit       1 qubit, x + measure, {record.get('shots')} shots")
	print(f"  repeats       {record.get('repeat')} per library")
	print()

	counting = record.get("connection_counting", {}).get("supported")
	header = (f"  {'library':<8} {'prep':>11} {'submit':>11} {'wait':>11}"
			f" {'fetch':>11} {'total':>11}")
	if counting:
		header += f" {'conns':>7}"
	print("PER RUN (median)")
	print(header)

	for library, entry in record.get("libraries", {}).items():
		if "error" in entry:
			print(f"  {library:<8} unavailable: {entry['error']}")
			continue
		runs = entry["runs"]
		row = (f"  {library:<8} {fmt_ms(_median(runs, 'prep_seconds')):>11}"
				f" {fmt_ms(_median(runs, 'submit_seconds')):>11}"
				f" {fmt_ms(_median(runs, 'wait_seconds')):>11}"
				f" {fmt_ms(_median(runs, 'result_fetch_seconds')):>11}"
				f" {fmt_ms(_median(runs, 'total_seconds')):>11}")
		if counting:
			conns = _median(runs, "connections")
			row += f" {conns:>7.0f}" if conns is not None else f" {'n/a':>7}"
		print(row)
	print()
	print("  prep    = everything before submit: transcode + payload assembly.")
	print("            Derived as total minus submit/wait/fetch. For the shim")
	print("            drivers this is local work; the native path also")
	print("            re-fetches the dynamic architecture here, having no")
	print("            introspection cache, so its prep includes a round trip.")
	print("  wait    = polling to terminal; includes device queue time, which")
	print("            neither interface reports separately.")
	if counting:
		print("  conns   = TCP connections opened during the run. Execution")
		print("            polls, so a per-request reconnect multiplies.")
	print()
	for library, entry in record.get("libraries", {}).items():
		if "error" not in entry and entry["runs"]:
			print(f"  {library}: counts {entry['runs'][0]['counts']}")

	# Provider-reported phase durations. The point of showing these is which
	# paths have none: queue wait and execution are separable only where the
	# provider's own timeline survives to the caller.
	print()
	print("PROVIDER-REPORTED TIMING (from the device's job timeline)")
	for library, entry in record.get("libraries", {}).items():
		if "error" in entry or not entry["runs"]:
			continue
		durations = entry["runs"][0].get("provider_durations")
		if not durations:
			print(f"  {library:<8} none - this path does not surface the "
					"provider's timeline")
			continue
		queue = durations.get("queue_wait_received_to_validation_started")
		execution = durations.get("execution")
		total = durations.get("server_total_created_to_completed")
		print(f"  {library:<8} queue {fmt_ms(queue)}"
				f"   execution {fmt_ms(execution)}"
				f"   server total {fmt_ms(total)}")


def parse_args():
	parser = argparse.ArgumentParser(
		description="Measure QRMI vs QDMI vs native circuit-execution cost. USES QPU TIME.")
	parser.add_argument("--device-id", default="ornl-iqm-20q")
	parser.add_argument("--libraries", default="qrmi,qdmi,native")
	parser.add_argument(
		"--shots", type=int, default=10,
		help="Shots per circuit. Kept small: this runs on real hardware.")
	parser.add_argument(
		"--repeat", type=int, default=1,
		help="Circuits per library. Each one consumes QPU time.")
	parser.add_argument("--circuit-timeout", type=float, default=600.0)
	parser.add_argument("--poll-interval", type=float, default=1.0)
	parser.add_argument(
		"--count-connections", action="store_true",
		help="Also count TCP connections opened per run (Linux only).")
	parser.add_argument("--json", action="store_true")
	args = parser.parse_args()
	args.libraries = [x.strip() for x in args.libraries.split(",") if x.strip()]
	return args


def main():
	args = parse_args()
	context = endpoint_context(args.device_id)
	count_port = endpoint_port(context) if args.count_connections else None

	record = {
		"schema": "qfw-execution-measurement-v0",
		"timestamp": datetime.now(timezone.utc).isoformat(),
		"host": os.uname().nodename,
		"shots": args.shots,
		"repeat": args.repeat,
		"context": context,
		"connection_counting": {
			"requested": bool(args.count_connections),
			"port": count_port,
			"supported": bool(count_port) and sys.platform.startswith("linux"),
		},
		"libraries": {},
	}

	failed = False
	for library in args.libraries:
		try:
			record["libraries"][library] = measure_library(
					library, args.device_id, args, count_port=count_port)
		except Exception as exc:
			record["libraries"][library] = {
				"library": library,
				"error": f"{type(exc).__name__}: {exc}",
			}
			failed = True

	if args.json:
		print(json.dumps(record, indent=2))
	else:
		render(record)
	return 1 if failed else 0


if __name__ == "__main__":
	sys.exit(main())
