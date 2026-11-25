import threading
import time


class _Launcher:
	def shutdown(self):
		pass


def _wait_for(check, timeout=5):
	deadline = time.monotonic() + timeout
	while time.monotonic() < deadline:
		if check():
			return True
		time.sleep(0.01)
	return check()


def _start_runners(monkeypatch, cores, num_workers):
	import util.qpm.util_qrc as util_qrc

	bound = []
	monkeypatch.setattr(
		util_qrc.os, "sched_getaffinity", lambda pid: set(cores))
	monkeypatch.setattr(
		util_qrc.os, "sched_setaffinity",
		lambda pid, mask: bound.append(next(iter(mask))))
	monkeypatch.setattr(util_qrc.svc_launcher, "Launcher", _Launcher)

	class _Runners(util_qrc.UTIL_QRC):
		def __init__(self):
			# Set before the runner threads start.
			self.checks = 0
			self.finished = threading.Event()
			self.completed = []
			super().__init__(num_workers=num_workers)

		def check_active_tasks(self, wid):
			self.checks += 1
			tasks = self.worker_pool[wid]['active_tasks']
			if tasks and self.finished.is_set():
				self.completed.extend(tasks)
				tasks.clear()

		def run_circuit_async(self, circ):
			return {'circ': circ}

	return _Runners(), bound


def _stop(runners):
	runners.shutdown()
	for worker in runners.worker_pool:
		worker['thread'].join(timeout=5)
		assert not worker['thread'].is_alive()


def test_idle_runners_block_until_shutdown(monkeypatch):
	runners, _ = _start_runners(monkeypatch, {0, 1}, num_workers=2)

	time.sleep(0.2)

	# Polling every 1 ms, the two runners would have checked ~400 times.
	assert runners.checks == 0
	_stop(runners)


def test_a_runner_polls_while_a_task_runs_then_blocks_again(monkeypatch):
	runners, _ = _start_runners(monkeypatch, {0}, num_workers=1)

	runners.worker_pool[0]['queue'].put('circuit')
	assert _wait_for(lambda: runners.checks > 20)
	runners.finished.set()
	assert _wait_for(lambda: runners.completed == [{'circ': 'circuit'}])
	checks = runners.checks
	time.sleep(0.2)

	assert runners.checks - checks <= 1
	_stop(runners)


def test_runners_bind_within_a_cpuset_that_does_not_start_at_zero(
		monkeypatch):
	runners, bound = _start_runners(
		monkeypatch, {32, 33, 34, 35}, num_workers=8)

	assert _wait_for(lambda: len(bound) == 8)

	assert sorted(bound) == [32, 32, 33, 33, 34, 34, 35, 35]
	_stop(runners)
