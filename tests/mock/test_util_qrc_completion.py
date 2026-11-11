"""How a QRC runner ends a circuit's run (openQSE/QFw#196)."""

import queue
import signal
import time

import pytest
from defw_exception import DEFwInProgress


class _Launcher:
	"""Reports a runner as running until it is killed."""

	def __init__(self, rc=None):
		self.rc = rc
		self.killed = []

	def status(self, pid):
		if self.rc is None:
			raise DEFwInProgress(f"{pid} is still running")
		return b"", b"", self.rc


class _Circuit:
	launch_time = creation_time = exec_time = completion_time = 0
	resources_consumed_time = 0

	def __init__(self, cid="cid-1", free_error=None):
		self.cid = cid
		self.state = "running"
		self.freed = []
		self.free_error = free_error
		self.info = {}

	def get_cid(self):
		return self.cid

	def set_fail(self):
		self.state = "failed"

	def set_exec_done(self):
		self.state = "done"

	def free_resources(self, circ, result=None):
		self.freed.append(result)
		if self.free_error is not None:
			raise self.free_error


def _qrc(monkeypatch, launcher, limit=600):
	import util.qpm.util_qrc as util_qrc

	monkeypatch.delenv("QFW_SITE_CONFIG", raising=False)
	qrc = util_qrc.UTIL_QRC(num_workers=1, start=False)
	qrc.launcher = launcher
	qrc.circuit_time_limit = limit
	qrc.worker_pool = [{'active_tasks': [], 'queue': queue.Queue()}]

	def killpg(pid, sig):
		launcher.killed.append((pid, sig))
		launcher.rc = -sig

	monkeypatch.setattr(util_qrc.os, "killpg", killpg)
	return qrc


def _task(tmp_path, circuit, pid=4321, age=0):
	qasm = tmp_path / f"{circuit.get_cid()}.qasm"
	qasm.write_text("OPENQASM 2.0;")
	return {
		'circ': circuit,
		'qasm_file': str(qasm),
		'pid': pid,
		'started': time.monotonic() - age,
		'telemetry': None,
	}


def test_a_runner_past_the_time_limit_is_killed_and_its_circuit_fails(
		monkeypatch, tmp_path):
	launcher = _Launcher()
	qrc = _qrc(monkeypatch, launcher, limit=600)
	circuit = _Circuit()
	qrc.worker_pool[0]['active_tasks'].append(
		_task(tmp_path, circuit, age=601))

	qrc.check_active_tasks(0)

	assert launcher.killed == [(4321, signal.SIGKILL)]
	assert circuit.state == "running"

	qrc.check_active_tasks(0)

	assert circuit.state == "failed"
	assert qrc.worker_pool[0]['active_tasks'] == []
	[result] = qrc.circuit_results
	assert result['rc'] == -signal.SIGKILL
	assert "killed after 600 s" in result['result']
	assert "qpm.circuit-time-limit-seconds" in result['result']
	assert circuit.freed == [result]


def test_a_runner_within_the_time_limit_is_left_alone(monkeypatch, tmp_path):
	launcher = _Launcher()
	qrc = _qrc(monkeypatch, launcher, limit=600)
	qrc.worker_pool[0]['active_tasks'].append(
		_task(tmp_path, _Circuit(), age=599))

	qrc.check_active_tasks(0)

	assert launcher.killed == []
	assert len(qrc.worker_pool[0]['active_tasks']) == 1


@pytest.mark.parametrize("limit, pid", [(None, 4321), (600, 0)])
def test_no_kill_without_a_limit_or_a_local_process(
		monkeypatch, tmp_path, limit, pid):
	# A launch on another host has pid 0, and killpg(0) would take down
	# the QPM's own process group.
	launcher = _Launcher()
	qrc = _qrc(monkeypatch, launcher, limit=limit)
	qrc.worker_pool[0]['active_tasks'].append(
		_task(tmp_path, _Circuit(), pid=pid, age=10_000))

	qrc.check_active_tasks(0)

	assert launcher.killed == []


def test_a_failure_while_finishing_a_circuit_leaves_the_runner_serving(
		monkeypatch, tmp_path):
	launcher = _Launcher(rc=-9)
	qrc = _qrc(monkeypatch, launcher)
	broken = _Circuit("broken", free_error=RuntimeError("rc=-7"))
	healthy = _Circuit("healthy")
	qrc.worker_pool[0]['active_tasks'].extend([
		_task(tmp_path, broken),
		_task(tmp_path, healthy),
	])

	qrc.check_active_tasks(0)

	assert qrc.worker_pool[0]['active_tasks'] == []
	assert [r['cid'] for r in qrc.circuit_results] == ["broken", "healthy"]
	assert healthy.state == "failed"


def test_circuit_time_limit_comes_from_the_site_file(tmp_path):
	from util.qpm.util_qrc import circuit_time_limit

	site = tmp_path / "site.yaml"

	assert circuit_time_limit(str(tmp_path / "missing.yaml")) == 600
	site.write_text("qpm:\n  completion-queues: {}\n")
	assert circuit_time_limit(str(site)) == 600
	site.write_text("qpm:\n  circuit-time-limit-seconds: 30\n")
	assert circuit_time_limit(str(site)) == 30
	site.write_text("qpm:\n  circuit-time-limit-seconds: 0\n")
	assert circuit_time_limit(str(site)) is None
	for bad in ("-1", "soon", "true"):
		site.write_text(f"qpm:\n  circuit-time-limit-seconds: {bad}\n")
		with pytest.raises(ValueError):
			circuit_time_limit(str(site))


def test_a_services_own_circuit_time_limit_overrides_the_site_file(tmp_path):
	from util.qpm.util_qrc import circuit_time_limit

	site = tmp_path / "site.yaml"
	site.write_text("qpm:\n  circuit-time-limit-seconds: 30\n")
	aer = {"name": "aer", "circuit-time-limit-seconds": 7200}

	assert circuit_time_limit(str(site), service=aer) == 7200
	assert circuit_time_limit(
		str(site), service=dict(aer, **{"circuit-time-limit-seconds": 0})) is None
	assert circuit_time_limit(str(site), service={"name": "aer"}) == 30
	with pytest.raises(ValueError, match=r"services\[aer\]"):
		circuit_time_limit(
			str(site), service=dict(aer, **{"circuit-time-limit-seconds": -1}))
