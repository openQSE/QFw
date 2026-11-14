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


class _LaunchRecorder:
	"""Records what each launch was given."""

	def __init__(self):
		self.launches = []
		self.killed = []

	def launch(self, cmd, env=None):
		self.launches.append((cmd, env))
		return 4321


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

	def set_launching(self):
		self.state = "launching"

	def set_running(self):
		self.state = "running"

	def free_resources(self, circ, result=None):
		self.freed.append(result)
		if self.free_error is not None:
			raise self.free_error


def _qrc(monkeypatch, launcher, limit=600,
	 source="qpm.circuit-time-limit-seconds"):
	import util.qpm.util_qrc as util_qrc

	monkeypatch.delenv("QFW_SITE_CONFIG", raising=False)
	qrc = util_qrc.UTIL_QRC(num_workers=1, start=False)
	qrc.launcher = launcher
	qrc.circuit_time_limit = limit
	qrc.circuit_time_limit_source = source
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


@pytest.mark.parametrize("limit, timeout", [
	(600, "605"),
	(2.5, "8"),
	(None, None),
])
def test_a_launch_gives_mpirun_a_time_limit_past_the_qpms(
		monkeypatch, tmp_path, limit, timeout):
	# Killing the launcher misses a runner that a DVM started, because the
	# runner is the DVM's child. MPIEXEC_TIMEOUT has the DVM end it, a few
	# seconds after the QPM has failed the circuit.
	import util.qpm.util_qrc as util_qrc

	launcher = _LaunchRecorder()
	qrc = _qrc(monkeypatch, launcher, limit=limit)
	qrc.form_cmd = lambda circ, qasm_file: f"mpirun --dvm file:uri {qasm_file}"
	# The mock suite's DEFw stub has no tmp-dir call.
	monkeypatch.setattr(util_qrc.cdefw_global, "get_defw_tmp_dir",
			    lambda: str(tmp_path), raising=False)
	monkeypatch.setattr(util_qrc, "openqasm2_text", lambda info: "OPENQASM 2.0;")

	task = qrc.run_circuit_async(_Circuit())

	[(cmd, env)] = launcher.launches
	assert cmd.startswith("mpirun --dvm")
	assert task['pid'] == 4321
	if timeout is None:
		assert env is None
	else:
		assert env == {"MPIEXEC_TIMEOUT": timeout}


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
	from util.qpm.util_qrc import circuit_time_limit_and_source

	site = tmp_path / "site.yaml"

	assert circuit_time_limit_and_source(str(tmp_path / "missing.yaml"))[0] == 600
	site.write_text("qpm:\n  completion-queues: {}\n")
	assert circuit_time_limit_and_source(str(site))[0] == 600
	site.write_text("qpm:\n  circuit-time-limit-seconds: 30\n")
	assert circuit_time_limit_and_source(str(site))[0] == 30
	site.write_text("qpm:\n  circuit-time-limit-seconds: 0\n")
	assert circuit_time_limit_and_source(str(site))[0] is None
	for bad in ("-1", "soon", "true"):
		site.write_text(f"qpm:\n  circuit-time-limit-seconds: {bad}\n")
		with pytest.raises(ValueError):
			circuit_time_limit_and_source(str(site))[0]


def test_a_services_own_circuit_time_limit_overrides_the_site_file(tmp_path):
	from util.qpm.util_qrc import circuit_time_limit_and_source

	def limit(service):
		return circuit_time_limit_and_source(str(site), service=service)[0]

	site = tmp_path / "site.yaml"
	site.write_text("qpm:\n  circuit-time-limit-seconds: 30\n")
	aer = {"name": "aer", "circuit-time-limit-seconds": 7200}

	assert limit(aer) == 7200
	assert limit(dict(aer, **{"circuit-time-limit-seconds": 0})) is None
	assert limit({"name": "aer"}) == 30
	with pytest.raises(ValueError, match=r"services\[aer\]"):
		limit(dict(aer, **{"circuit-time-limit-seconds": -1}))


def test_a_services_own_circuit_time_limit_is_named_as_its_source(tmp_path):
	from util.qpm.util_qrc import circuit_time_limit_and_source

	site = tmp_path / "site.yaml"
	site.write_text("qpm:\n  circuit-time-limit-seconds: 30\n")
	aer = {"name": "aer", "circuit-time-limit-seconds": 3}

	assert circuit_time_limit_and_source(str(site), service=aer) == (
		3, "services[aer].circuit-time-limit-seconds")
	assert circuit_time_limit_and_source(str(site), service={"name": "aer"}) == (
		30, "qpm.circuit-time-limit-seconds")


def test_a_runner_killed_by_its_services_limit_names_that_limit(
		monkeypatch, tmp_path):
	launcher = _Launcher()
	qrc = _qrc(monkeypatch, launcher, limit=3,
		   source="services[aer].circuit-time-limit-seconds")
	circuit = _Circuit()
	qrc.worker_pool[0]['active_tasks'].append(_task(tmp_path, circuit, age=4))

	qrc.check_active_tasks(0)
	qrc.check_active_tasks(0)

	[result] = qrc.circuit_results
	assert "services[aer].circuit-time-limit-seconds" in result['result']
	assert "qpm.circuit-time-limit-seconds" not in result['result']
