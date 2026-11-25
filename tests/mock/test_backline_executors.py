# Guards the Backline QPM's catalyst-executor processes
# (svc_backline_qpm/executors.py). The QPM starts one per reservation in its
# own session, and must stop the whole process group, including children the
# executor forked per connection, on release, on a crashed leader and when
# the QPM itself gets SIGTERM.
#
# catalyst-executor is stood in for by a shell script, so no Catalyst install
# is needed.

import os
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from svc_backline_qpm import executors


def _fake_binary(tmp_path, body):
	path = tmp_path / "catalyst-executor"
	path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
	path.chmod(path.stat().st_mode | stat.S_IEXEC)
	return str(path)


def test_start_waits_for_ready_and_stop_kills_the_group(tmp_path):
	binary = _fake_binary(tmp_path, 'echo "$@"; echo "Listening on 0.0.0.0:1"; sleep 60')
	running = executors.start(tmp_path, ["libdecoder.so"], binary=binary,
				   log_dir=tmp_path)
	host, port = running.address.rsplit(":", 1)
	assert host == socket.gethostname() and int(port) > 0
	log = Path(running.log_path).read_text()
	assert f"--bind=0.0.0.0:{port}" in log
	assert f"--plugin={tmp_path / 'libdecoder.so'}" in log
	assert running.process.poll() is None
	executors.stop(running)
	assert running.process.poll() is not None


def test_start_fails_when_the_executor_exits(tmp_path):
	binary = _fake_binary(tmp_path, "exit 3")
	with pytest.raises(executors.ExecutorStartError, match="did not become ready"):
		executors.start(tmp_path, [], binary=binary, log_dir=tmp_path)


def test_start_times_out_and_cleans_up(tmp_path):
	binary = _fake_binary(tmp_path, "sleep 60")
	t0 = time.monotonic()
	with pytest.raises(executors.ExecutorStartError):
		executors.start(tmp_path, [], binary=binary, log_dir=tmp_path,
				timeout_s=0.5)
	assert time.monotonic() - t0 < 10


def test_stop_kills_children_after_the_leader_died(tmp_path):
	# The executor forks a child per connection; a crashed leader must not
	# leave one holding the port.
	child_pid = tmp_path / "child.pid"
	binary = _fake_binary(
		tmp_path, f'sleep 60 & echo $! > {child_pid}; '
		'echo "Listening on 0.0.0.0:1"; wait')
	running = executors.start(tmp_path, [], binary=binary, log_dir=tmp_path)
	child = int(child_pid.read_text())
	running.process.kill()
	running.process.wait()
	executors.stop(running)
	time.sleep(0.2)
	with pytest.raises(ProcessLookupError):
		os.kill(child, 0)


def test_start_reports_a_missing_binary_as_a_start_error(tmp_path):
	with pytest.raises(executors.ExecutorStartError):
		executors.start(tmp_path, [], binary=str(tmp_path / "absent"),
				log_dir=tmp_path)


def test_start_reports_a_missing_log_dir_as_a_start_error(tmp_path):
	binary = _fake_binary(tmp_path, 'echo "Listening on 0.0.0.0:1"; sleep 60')
	with pytest.raises(executors.ExecutorStartError):
		executors.start(tmp_path, [], binary=binary,
				log_dir=tmp_path / "absent")


def test_sigterm_to_the_qpm_process_stops_its_executors(tmp_path):
	# qfw-site-services stop / qfw-teardown SIGTERM the QPM; executors run in
	# their own session, so the QPM must stop them itself.
	binary = _fake_binary(tmp_path, 'echo "Listening on 0.0.0.0:1"; sleep 60')
	here = Path(executors.__file__).resolve().parent
	script = (
		"import sys, time\n"
		"import executors\n"
		f"r = executors.start({str(tmp_path)!r}, [], binary={binary!r}, "
		f"log_dir={str(tmp_path)!r})\n"
		"print(r.process.pid, flush=True)\n"
		"time.sleep(60)\n")
	qpm = subprocess.Popen([sys.executable, "-c", script], cwd=here,
			       stdout=subprocess.PIPE, text=True)
	executor_pid = int(qpm.stdout.readline())
	qpm.send_signal(signal.SIGTERM)
	assert qpm.wait(timeout=10) != 0
	time.sleep(0.2)
	with pytest.raises(ProcessLookupError):
		os.kill(executor_pid, 0)
