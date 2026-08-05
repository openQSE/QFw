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
from urllib.parse import urlsplit

# Introspection calls served by both libraries, so the same set can be timed on
# each. Kept in a fixed order because the first one is the cold-path call.
CALLS = ("get_device_info", "get_coupling_graph", "get_calibration_snapshot")


class MeasurementError(Exception):
	"""A sample was taken but cannot be trusted."""


# --- connection counting ------------------------------------------------
#
# How many TCP connections a library opens to reach the same data is the
# difference that explains most of the cold-cost gap: a client that pools and
# reuses one connection pays one TLS handshake, one that opens a fresh
# connection per request pays a handshake every time. Over a tunnel a handshake
# costs about as much as a request, so this is worth counting rather than
# inferring from timings.
#
# Counted by sampling /proc for this process's own sockets whose remote port is
# the endpoint's, and accumulating distinct local endpoints. Filtering by our
# own socket inodes keeps other processes on the node out of the count.
#
# Limits: Linux only, and a poll can miss a connection shorter-lived than the
# sample interval, so the count is a lower bound. Off by default because the
# sampler runs concurrently with the timed calls.


# The sampler runs in a SUBPROCESS, not a thread. QDMI's session init is a
# single blocking call into the C++ device library which holds the GIL for its
# whole duration, so an in-process Python sampler is frozen exactly when the
# connections it should be counting are being opened -- it reported zero for
# QDMI while `ss` showed five. A separate process is not subject to the GIL.
_SAMPLER_SOURCE = r'''
import os, sys, time

pid, port, interval = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
out_path, stop_path = sys.argv[4], sys.argv[5]
seen = set()

def socket_inodes():
    found = set()
    try:
        fds = os.listdir("/proc/%d/fd" % pid)
    except OSError:
        return found
    for fd in fds:
        try:
            target = os.readlink("/proc/%d/fd/%s" % (pid, fd))
        except OSError:
            continue
        if target.startswith("socket:["):
            found.add(target[8:-1])
    return found

def peers():
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as stream:
                next(stream, None)
                for line in stream:
                    fields = line.split()
                    if len(fields) < 10:
                        continue
                    try:
                        if int(fields[2].rsplit(":", 1)[1], 16) != port:
                            continue
                    except (IndexError, ValueError):
                        continue
                    yield fields[1], fields[9]
        except OSError:
            continue

# Results go to a file and shutdown is signalled by a file, not by pipes and
# signals: kill()+communicate() proved unreliable in the container this runs in
# (a trivial child reproduced the same hang), and a sampler that cannot be
# stopped or read is worse than no sampler.
with open(out_path, "a", buffering=1) as sink:
    while not os.path.exists(stop_path):
        ours = socket_inodes()
        for local, inode in peers():
            if inode in ours and local not in seen:
                seen.add(local)
                sink.write(local + "\n")   # line-buffered, so already flushed
        time.sleep(interval)
'''


class ConnectionSampler:
	"""Count distinct TCP connections this process opens to a port.

	Use as a context manager; `endpoints` holds the local endpoints observed.
	Counting *new* connections for a later phase means differencing against an
	earlier phase's set -- a pooled connection stays open and visible, so
	observing it again is not the same as opening it again.
	"""

	def __init__(self, port, interval=0.02, enabled=True):
		self._port = port
		self._interval = interval
		self._enabled = bool(enabled and sys.platform.startswith("linux")
				and port)
		self._proc = None
		self._dir = None
		self.endpoints = set()

	@property
	def supported(self):
		return self._enabled

	def __enter__(self):
		if not self._enabled:
			return self
		import tempfile
		self._dir = tempfile.mkdtemp(prefix="qfw-connsample-")
		self._out = os.path.join(self._dir, "endpoints")
		self._stop = os.path.join(self._dir, "stop")
		open(self._out, "w").close()
		try:
			self._proc = subprocess.Popen(
				[sys.executable, "-c", _SAMPLER_SOURCE,
					str(os.getpid()), str(self._port), str(self._interval),
					self._out, self._stop],
				stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
		except OSError:
			self._proc = None
			self._enabled = False
		return self

	def __exit__(self, *exc):
		if self._proc is None:
			return False
		open(self._stop, "w").close()
		# Give the sampler a couple of poll intervals to notice and exit.
		deadline = time.monotonic() + max(1.0, self._interval * 10)
		while time.monotonic() < deadline and self._proc.poll() is None:
			time.sleep(self._interval)
		if self._proc.poll() is None:
			self._proc.kill()
		try:
			with open(self._out, "r", encoding="utf-8") as stream:
				self.endpoints = {line.strip() for line in stream
						if line.strip()}
		except OSError:
			self.endpoints = set()
		import shutil
		shutil.rmtree(self._dir, ignore_errors=True)
		return False


def _qubit_count(record):
	# Qubit count from a qhw-device-v1 record, or None if this is not one.
	if isinstance(record, dict) and isinstance(record.get("qubits"), list):
		return len(record["qubits"])
	return None


def _require_real_payload(record):
	# A timing is only meaningful if the call actually returned device data.
	#
	# This check exists because QRMI's target() does not raise when its REST
	# fetches fail: each field is individually guarded and replaced with null,
	# so an unreachable endpoint yields a successful call returning an empty
	# document. Timing that path produces plausible-looking numbers -- observed
	# at ~45 ms cold against a dead endpoint -- that measure nothing but the
	# speed of failing. Worse, they flatter QRMI relative to QDMI, which raises
	# on a failed session init and so reports no sample at all.
	#
	# Requiring a non-empty qubit list turns that silent case into a loud one.
	qubits = _qubit_count(record)
	if not qubits:
		raise MeasurementError(
			"introspection returned no qubits, so this sample is not a "
			"measurement of a working call. The endpoint is most likely "
			"unreachable; note that QRMI reports this as success with null "
			"fields rather than raising.")
	return qubits


def _driver(library, descriptor):
	from svc_lib_qpm.drivers import QdmiDriver, QrmiDriver

	if library == "qrmi":
		return QrmiDriver(descriptor)
	if library == "qdmi":
		return QdmiDriver(descriptor)
	raise ValueError(f"unknown library {library!r}")


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
	driver = _driver(library, descriptor)
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
	result["qubits_seen"] = _require_real_payload(first_record)
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
					_require_real_payload(returned)
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


def endpoint_context(device_id):
	"""Endpoint identity for the record. Never returns the token."""
	try:
		from svc_lib_qpm.descriptor import resolve_descriptor
		from svc_lib_qpm.drivers import QrmiDriver

		access = QrmiDriver(resolve_descriptor(device_id))._access()
		return {
			"base_url": access.get("base_url"),
			"provider_device_id": access.get("provider_device_id"),
		}
	except Exception as exc:
		return {"error": f"{type(exc).__name__}: {exc}"}


def _endpoint_port(context):
	# Port the libraries connect to, for connection counting.
	base_url = (context or {}).get("base_url")
	if not base_url:
		return None
	parts = urlsplit(base_url)
	if parts.port:
		return parts.port
	return 443 if parts.scheme == "https" else 80


def run_once(args):
	context = endpoint_context(args.device_id)
	count_port = _endpoint_port(context) if args.count_connections else None
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


def _fmt(seconds):
	return f"{seconds * 1000:.1f} ms"


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
		row = (f"  {library:<8} {_fmt(statistics.median(opens)):>12}"
				f" {_fmt(statistics.median(firsts)):>12}"
				f" {_fmt(statistics.median(colds)):>12}")
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
			cells.append(_fmt(statistics.median(samples)) if samples else "n/a")
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
