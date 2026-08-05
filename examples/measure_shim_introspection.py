#!/usr/bin/env python
"""Measure QRMI vs QDMI device-introspection cost, cold and warm.

Read-only: this submits no circuits and consumes no QPU time.

The two interfaces reach the same device data through different shapes, and the
cost lands in different places:

  QRMI   QuantumResource.target() issues three IQM REST calls (dynamic quantum
         architecture, calibration set, quality metrics) and the shim driver
         caches the parsed document per driver instance. The network cost is
         paid by the FIRST introspection call; later calls read the cache.

  QDMI   opening the device (FoMaC add_dynamic_device_library) initializes a
         session, and the IQM device library fetches its data during that init.
         The network cost is paid at OPEN; later property queries are local.

So a fair comparison has to separate opening from querying, otherwise QRMI
looks slow at the first call and QDMI looks slow at startup while both are
doing the same work at different moments. This measures three phases per
library:

  open        construct the underlying handle (QRMI resource / QDMI session)
  first call  the first get_device_info() after open
  warm calls  repeated introspection on the same instance

"cold" is open + first call: the total cost of going from nothing to one
answer. That is the number that matters to a scheduler deciding whether it can
afford to introspect per job.

A true cold sample needs a fresh process -- both drivers cache on the instance,
and the FoMaC loader registers the device library process-wide. --repeat
therefore re-executes this script in subprocesses rather than looping in-place.

Run it inside the container, with device access configured (see the
QFw-SLURM-Cluster IQM-ACCESS.md):

  docker exec c5 bash -c 'source /opt/qfw/qhpc/QFw/setup/qfw_activate \\
      >/dev/null 2>&1 && source "$QFW_IMAGE_VENV/bin/activate" \\
      && python -u /opt/qfw/qhpc/QFw/examples/measure_shim_introspection.py'

No SLURM allocation is required: QRMI's target() is not reservation-bound and
QDMI needs only an initialized session.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from measurement_support import (  # noqa: E402
	ConnectionSampler, driver_for, endpoint_context,
	endpoint_port, fmt_ms, require_qubits)

# Introspection calls served by both libraries, so the same set can be timed on
# each. Kept in a fixed order because the first one is the cold-path call.
CALLS = ("get_device_info", "get_coupling_graph", "get_calibration_snapshot")


def _open_handle(library, driver):
	# Force the lazy handle without issuing an introspection call, so the open
	# cost is attributed separately from the first query. These are the private
	# accessors the drivers use internally; there is no public "open" call.
	if library == "qrmi":
		return driver._qpu()
	return driver._device()


def measure_library(library, device_id, warm_iterations, count_port=None):
	"""One cold sample plus warm_iterations warm samples. Returns a dict."""
	from svc_lib_qpm.descriptor import resolve_descriptor

	descriptor = resolve_descriptor(device_id)
	result = {"library": library, "device_id": device_id}

	construct_start = time.perf_counter()
	driver = driver_for(library, descriptor)
	result["construct_seconds"] = time.perf_counter() - construct_start

	# Cold phase: open + first call, with connections counted across both,
	# since the libraries do their fetching in different phases.
	with ConnectionSampler(count_port, enabled=count_port is not None) as cold_conns:
		open_start = time.perf_counter()
		_open_handle(library, driver)
		result["open_seconds"] = time.perf_counter() - open_start

		first_start = time.perf_counter()
		first_record = getattr(driver, CALLS[0])()
		result["first_call_seconds"] = time.perf_counter() - first_start

	# Validate before reporting: a fast failure must not read as a fast call.
	result["qubits_seen"] = require_qubits(first_record)
	result["cold_seconds"] = result["open_seconds"] + result["first_call_seconds"]
	result["cold_connections"] = (
			len(cold_conns.endpoints) if cold_conns.supported else None)

	warm = {}
	# Counted separately: a warm path that opens no connection is direct
	# evidence that repeat introspection is served locally rather than refetched.
	with ConnectionSampler(count_port, enabled=count_port is not None) as warm_conns:
		for call in CALLS:
			samples = []
			for _ in range(warm_iterations):
				start = time.perf_counter()
				returned = getattr(driver, call)()
				samples.append(time.perf_counter() - start)
				if call == "get_device_info":
					require_qubits(returned)
			warm[call] = {
				"samples": samples,
				"median_seconds": statistics.median(samples) if samples else None,
				"min_seconds": min(samples) if samples else None,
				"max_seconds": max(samples) if samples else None,
			}
	result["warm"] = warm
	# Only connections the warm phase actually OPENED: a pooled connection left
	# open by the cold phase stays visible, and re-observing it is not a reopen.
	result["warm_connections"] = (
			len(warm_conns.endpoints - cold_conns.endpoints)
			if warm_conns.supported else None)
	return result


def run_once(args):
	context = endpoint_context(args.device_id)
	count_port = endpoint_port(context) if args.count_connections else None
	record = {
		"schema": "qfw-introspection-measurement-v0",
		"timestamp": datetime.now(timezone.utc).isoformat(),
		"host": os.uname().nodename,
		"warm_iterations": args.warm_iterations,
		"context": context,
		"connection_counting": {
			"requested": bool(args.count_connections),
			"port": count_port,
			"supported": bool(count_port) and sys.platform.startswith("linux"),
		},
		"libraries": {},
	}
	for library in args.libraries:
		try:
			record["libraries"][library] = measure_library(
					library, args.device_id, args.warm_iterations,
					count_port=count_port)
		except Exception as exc:
			record["libraries"][library] = {
				"library": library,
				"error": f"{type(exc).__name__}: {exc}",
			}
	return record


def run_repeated(args):
	# Each cold sample needs a clean process: both drivers cache per instance,
	# and the FoMaC loader registers its device library process-wide, so a
	# second in-process "cold" open would not be cold.
	child = [
		sys.executable, "-u", os.path.abspath(__file__),
		"--json",
		"--device-id", args.device_id,
		"--warm-iterations", str(args.warm_iterations),
		"--libraries", ",".join(args.libraries),
	]
	if args.count_connections:
		child.append("--count-connections")
	records = []
	for index in range(args.repeat):
		completed = subprocess.run(child, capture_output=True, text=True)
		if completed.returncode != 0:
			sys.stderr.write(
				f"[measure] iteration {index + 1} failed:\n{completed.stderr}")
			continue
		try:
			records.append(json.loads(completed.stdout))
		except json.JSONDecodeError:
			sys.stderr.write(
				f"[measure] iteration {index + 1} produced unparsable output\n")
	return records



def render(records):
	first = records[0]
	context = first.get("context", {})
	libraries = list(first.get("libraries", {}).keys())

	device_id = "unknown"
	for entry in first.get("libraries", {}).values():
		if entry.get("device_id"):
			device_id = entry["device_id"]
			break
	alias = context.get("provider_device_id", "unknown")

	print("QFw shim introspection cost -- QRMI vs QDMI")
	print(f"  endpoint      {context.get('base_url', 'unknown')}")
	print(f"  device        {device_id}  (provider alias {alias})")
	print(f"  host          {first.get('host', 'unknown')}")
	print(f"  cold samples  {len(records)}"
			f"   warm iterations {first.get('warm_iterations')}")
	print()

	counting = first.get("connection_counting", {}).get("supported")

	print("COLD  (open + first call, one fresh process per sample)")
	header = f"  {'library':<8} {'open':>12} {'first call':>12} {'cold total':>12}"
	if counting:
		header += f" {'conns':>7}"
	print(header)
	for library in libraries:
		opens, firsts, colds, conns = [], [], [], []
		for record in records:
			entry = record["libraries"].get(library, {})
			if "error" in entry:
				continue
			opens.append(entry["open_seconds"])
			firsts.append(entry["first_call_seconds"])
			colds.append(entry["cold_seconds"])
			if entry.get("cold_connections") is not None:
				conns.append(entry["cold_connections"])
		if not colds:
			error = first["libraries"].get(library, {}).get("error", "no data")
			print(f"  {library:<8} unavailable: {error}")
			continue
		row = (f"  {library:<8} {fmt_ms(statistics.median(opens)):>12}"
				f" {fmt_ms(statistics.median(firsts)):>12}"
				f" {fmt_ms(statistics.median(colds)):>12}")
		if counting:
			row += f" {(statistics.median(conns) if conns else 0):>7.0f}"
		print(row)
	if counting:
		print("  conns = distinct TCP connections opened to the endpoint;")
		print("          a client that pools pays one TLS handshake, one that")
		print("          reconnects per request pays a handshake every time.")
	print()

	print("WARM  (repeat calls on the same instance, median)")
	header = f"  {'library':<8}" + "".join(f"{c.replace('get_', ''):>26}"
			for c in CALLS)
	print(header)
	for library in libraries:
		cells = []
		for call in CALLS:
			samples = []
			for record in records:
				entry = record["libraries"].get(library, {})
				warm = entry.get("warm", {}).get(call)
				if warm:
					samples.extend(warm["samples"])
			cells.append(fmt_ms(statistics.median(samples)) if samples else "n/a")
		row = f"  {library:<8}" + "".join(f"{c:>26}" for c in cells)
		if counting:
			warm_conns = [record["libraries"].get(library, {}).get("warm_connections")
					for record in records]
			warm_conns = [c for c in warm_conns if c is not None]
			row += f"   conns {int(max(warm_conns)) if warm_conns else 0}"
		print(row)
	if counting:
		print("  warm conns should be 0: repeat introspection is served from")
		print("  the driver cache (QRMI) or the open session (QDMI), not refetched.")
	print()
	print("Interpretation: QRMI pays its three REST round-trips at the first")
	print("call (target(), then cached); QDMI pays them at open (session init),")
	print("after which property queries are local. Compare the cold totals, not")
	print("the individual phases.")


def parse_args():
	parser = argparse.ArgumentParser(
		description="Measure QRMI vs QDMI introspection cost (no QPU time).")
	parser.add_argument(
		"--device-id", default="ornl-iqm-20q",
		help="QFw device id to resolve the descriptor from.")
	parser.add_argument(
		"--libraries", default="qrmi,qdmi",
		help="Comma-separated libraries to measure.")
	parser.add_argument(
		"--warm-iterations", type=int, default=5,
		help="Warm calls per introspection API, per cold sample.")
	parser.add_argument(
		"--repeat", type=int, default=1,
		help="Independent cold samples, each in a fresh subprocess.")
	parser.add_argument(
		"--count-connections", action="store_true",
		help="Also count distinct TCP connections opened to the endpoint "
		     "(Linux only). Off by default: the sampling thread runs "
		     "concurrently with the timed calls.")
	parser.add_argument(
		"--json", action="store_true",
		help="Emit the raw JSON record instead of the table.")
	args = parser.parse_args()
	args.libraries = [item.strip() for item in args.libraries.split(",")
			if item.strip()]
	return args


def main():
	args = parse_args()

	if args.repeat > 1:
		records = run_repeated(args)
		if not records:
			sys.stderr.write("[measure] no successful iterations\n")
			return 1
	else:
		records = [run_once(args)]

	if args.json:
		print(json.dumps(records[0] if len(records) == 1 else records, indent=2))
	else:
		render(records)

	for record in records:
		for entry in record.get("libraries", {}).values():
			if "error" in entry:
				return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
