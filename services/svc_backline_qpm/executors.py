# catalyst-executor processes owned by the Backline QPM, one per reservation.
#
# The executor hosts the reservation's controller and decoder code (dispatched
# by the client over ORC) in one process, so memcpy can connect them. Remote
# Backline nodes name libraries by bare filename, so the executor runs with
# Catalyst's lib/ as working directory and on LD_LIBRARY_PATH.

import atexit
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

RUNTIME_PLUGINS = ("librt_transport.so", "librt_capi.so")
# First line catalyst-executor prints once bound (as matched by
# catalyst.executor.utils.OutputPatterns).
_READY = "Listening on"


class ExecutorStartError(RuntimeError):
	pass


@dataclass(eq=False)
class Running:
	address: str
	process: object
	log_path: str


def _free_port():
	with socket.socket() as s:
		s.bind(("", 0))
		return s.getsockname()[1]


def start(catalyst_lib, plugins, binary=None, log_dir=None, timeout_s=30.0):
	lib = Path(catalyst_lib)
	port = _free_port()
	log_dir = Path(log_dir or os.environ.get("QFW_LOG_DIR") or
		       tempfile.gettempdir())
	log_path = str(log_dir / f"backline-executor-{port}.log")
	# TODO(security): the executor listens on every interface and runs any
	# program a client sends, unauthenticated, as the QPM's user. Bind it to
	# the client-facing interface and check a per-reservation secret.
	argv = [binary or str(lib / "catalyst-executor"),
		f"--bind=0.0.0.0:{port}"]
	argv += [f"--plugin={lib / p}" for p in plugins]
	env = dict(os.environ)
	env["LD_LIBRARY_PATH"] = os.pathsep.join(
		p for p in (str(lib), os.environ.get("LD_LIBRARY_PATH")) if p)
	try:
		with open(log_path, "w") as log:
			proc = subprocess.Popen(argv, cwd=lib, env=env,
						stdin=subprocess.DEVNULL, stdout=log,
						stderr=subprocess.STDOUT,
						start_new_session=True)
	except OSError as e:
		raise ExecutorStartError(f"cannot start catalyst-executor: {e}") from e
	running = Running(f"{socket.gethostname()}:{port}", proc, log_path)
	deadline = time.monotonic() + timeout_s
	while time.monotonic() < deadline and proc.poll() is None:
		if _READY in Path(log_path).read_text(errors="replace"):
			_live.add(running)
			return running
		time.sleep(0.1)
	stop(running)
	raise ExecutorStartError(
		f"catalyst-executor did not become ready; see {log_path}")


def stop(running):
	# The executor forks a child per connection, so stop the whole group, even
	# when the leader has already exited: the group lives while any child does.
	_live.discard(running)
	proc = running.process
	if proc is None:
		return
	try:
		os.killpg(proc.pid, signal.SIGTERM)
	except ProcessLookupError:
		return
	try:
		proc.wait(timeout=5)
	except subprocess.TimeoutExpired:
		pass
	try:
		os.killpg(proc.pid, signal.SIGKILL)
	except (ProcessLookupError, PermissionError):
		pass  # group already gone (macOS reports EPERM for an emptied group)
	proc.wait()


# Executors run in their own session, so the SIGTERM that qfw-site-services
# stop / qfw-teardown send to the QPM's process group misses them; and DEFw
# does not reach the QPM's shutdown on SIGTERM. Stop them here instead.
_live = set()


def _stop_all():
	for running in list(_live):
		stop(running)


def _on_sigterm(signum, frame, previous=None):
	_stop_all()
	if callable(previous):
		previous(signum, frame)
	else:
		signal.signal(signum, signal.SIG_DFL)
		os.kill(os.getpid(), signum)


atexit.register(_stop_all)
if threading.current_thread() is threading.main_thread():
	_previous = signal.getsignal(signal.SIGTERM)
	signal.signal(signal.SIGTERM, lambda signum, frame: _on_sigterm(
		signum, frame, _previous))
