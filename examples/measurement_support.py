"""Shared helpers for the QRMI/QDMI measurement scripts in this directory.

Kept in one module rather than copied per script: the connection sampler
manages a subprocess, and that is not code worth maintaining twice.
"""

import os
import subprocess
import sys
import time
from urllib.parse import urlsplit


class MeasurementError(Exception):
	"""A sample was taken but cannot be trusted."""


# --- payload validation -------------------------------------------------
#
# A timing is only meaningful if the call actually did the work. QRMI's
# target() does not raise when its REST fetches fail -- each field is guarded
# individually and replaced with null -- so an unreachable endpoint yields a
# successful call returning an empty document. Timing that path produces
# plausible-looking numbers that measure nothing but the speed of failing, and
# flatter QRMI against QDMI, which raises instead. Validate the payload, never
# just the absence of an exception.


def require_qubits(record):
	"""Qubit count from a qhw device record; raise if it is empty."""
	qubits = record.get("qubits") if isinstance(record, dict) else None
	if not isinstance(qubits, list) or not qubits:
		raise MeasurementError(
			"introspection returned no qubits, so this sample is not a "
			"measurement of a working call. The endpoint is most likely "
			"unreachable; note that QRMI reports this as success with null "
			"fields rather than raising.")
	return len(qubits)


def require_counts(record):
	"""Shot counts from a qhw-result-v1 record; raise if the run did not land."""
	result = record.get("result") if isinstance(record, dict) else None
	counts = (result or {}).get("counts") if isinstance(result, dict) else None
	if not counts or not sum(int(v) for v in counts.values()):
		raise MeasurementError(
			"execution returned no shot counts, so this sample did not "
			"measure a completed circuit.")
	return counts


# --- connection counting ------------------------------------------------
#
# How many TCP connections a library opens to reach the same data is the
# difference that explains most of the cold-cost gap: a client that pools and
# reuses one connection pays one TLS handshake, one that opens a fresh
# connection per request pays a handshake every time. Over a wide-area path a
# handshake costs about as much as a request, so this is worth counting rather
# than inferring from timings.
#
# Limits: Linux only, and a poll can miss a connection shorter-lived than the
# sample interval, so a count is a lower bound.

_SAMPLER_SOURCE = r'''
import os, sys, time

pid, port, interval = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
out_path, stop_path = sys.argv[4], sys.argv[5]
seen = set()

# Backstop lifetime. A phase takes seconds; this only bounds a sampler whose
# parent vanished between the liveness checks below.
MAX_LIFETIME = 300.0
started = time.monotonic()

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

with open(out_path, "a", buffering=1) as sink:
    while not os.path.exists(stop_path):
        # Never outlive the parent. Without these two guards a parent that dies
        # without running its cleanup -- killed, crashed, interrupted -- leaves
        # this process polling forever. socket_inodes() fails softly when the
        # parent is gone, so the loop would spin rather than error out. During
        # development that left 21 orphaned samplers burning 145% CPU.
        if not os.path.isdir("/proc/%d" % pid):
            break
        if time.monotonic() - started > MAX_LIFETIME:
            break
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

	Runs in a subprocess, not a thread: a blocking call into a C extension
	holds the GIL for its whole duration, and an in-process sampler is then
	frozen exactly while the connections it should count are being opened.

	Results and shutdown both go through files rather than pipes and signals,
	because Popen.kill() followed by communicate() hangs in the container this
	is used in -- reproducible with a trivial child process.
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


# --- endpoint context ---------------------------------------------------


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


def endpoint_port(context):
	"""Port the libraries connect to, for connection counting."""
	base_url = (context or {}).get("base_url")
	if not base_url:
		return None
	parts = urlsplit(base_url)
	if parts.port:
		return parts.port
	return 443 if parts.scheme == "https" else 80


class NativeAdapter:
	"""QFw's native IQM path, presented with the same surface as the drivers.

	`svc_iqm_qpm.util_iqm.IQMServiceClient` already exposes get_device_info,
	get_coupling_graph, get_calibration_snapshot, run_circuit and
	get_last_job_timing. Two return shapes differ, so they are adapted here
	rather than by touching the native service, which is deliberately
	unmodified by the shim work:

	  run_circuit          returns {"counts", "qhw_result"}; the drivers return
	                       the qhw record directly.
	  get_last_job_timing  returns an iqm-timing-summary-v1 record whose
	                       client-side spans live under "client_wall_seconds".

	That second difference is worth more than the adaptation. The native record
	also carries `durations_seconds`, derived from the IQM job timeline:
	queue wait, validation, compilation, execution, post-processing. That is
	provider-side timing which neither QRMI nor QDMI passes through, so the
	native arm shows the information exists and is lost at the interface rather
	than absent at the provider. `provider_durations()` exposes it.
	"""

	name = "native"

	def __init__(self):
		from svc_iqm_qpm.util_iqm import IQMServiceClient
		self._client = IQMServiceClient()
		self._last_summary = None

	def open(self):
		return self._client.client()

	def get_device_info(self):
		return self._client.get_device_info()

	def get_coupling_graph(self, calibration_set_id=None):
		return self._client.get_coupling_graph(calibration_set_id)

	def get_calibration_snapshot(self, calibration_set_id=None):
		return self._client.get_calibration_snapshot(calibration_set_id)

	def run_circuit(self, circuit):
		out = self._client.run_circuit(circuit)
		if isinstance(out, dict) and "qhw_result" in out:
			return out["qhw_result"]
		return out

	def get_last_job_timing(self, cid=None):
		summary = self._client.get_last_job_timing(cid) or {}
		self._last_summary = summary
		wall = summary.get("client_wall_seconds") or {}
		return {"timing": {
			"submit_seconds": wall.get("submit"),
			"wait_seconds": wall.get("wait"),
			"result_fetch_seconds": wall.get("result_fetch"),
			"total_wall_seconds": wall.get("total"),
		}}

	def provider_durations(self):
		"""Provider-reported phase durations, or None if this path has none."""
		return (self._last_summary or {}).get("durations_seconds")


def driver_for(library, descriptor):
	from svc_lib_qpm.drivers import QdmiDriver, QrmiDriver

	if library == "qrmi":
		return QrmiDriver(descriptor)
	if library == "qdmi":
		return QdmiDriver(descriptor)
	if library == "native":
		return NativeAdapter()
	raise ValueError(f"unknown library {library!r}")


def open_handle(library, driver):
	"""Force the lazy handle without issuing a call, so open is timed apart."""
	if library == "qrmi":
		return driver._qpu()
	if library == "qdmi":
		return driver._device()
	return driver.open()


def fmt_ms(seconds):
	return "n/a" if seconds is None else f"{seconds * 1000:.1f} ms"
