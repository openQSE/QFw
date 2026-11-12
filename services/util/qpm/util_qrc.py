from api_events import Event
from defw_util import print_thread_stack_trace_to_logger
import logging
import time
import queue
import signal
import threading
import traceback
import sys
import os
from defw_exception import DEFwExecutionError, DEFwInProgress, DEFwOutOfResources
import svc_launcher
import cdefw_global
from util.circuit_payload import openqasm2_text
from util import instrumentation

sys.path.append(os.path.split(os.path.abspath(__file__))[0])

# A circuit's runner gets this long before the QPM kills it and fails the
# circuit. PRTE can lose a finished runner's exit, and with nothing to stop
# it the launcher then waits forever while every later circuit queues
# behind it (openQSE/QFw#196). Set qpm.circuit-time-limit-seconds in the
# site file to change it; 0 turns the limit off.
SITE_CONFIG_ENV = "QFW_SITE_CONFIG"
CIRCUIT_TIME_LIMIT_KEY = "circuit-time-limit-seconds"
SERVICE_CONFIG_ENV = "QFW_SERVICE_CONFIG"
DEFAULT_CIRCUIT_TIME_LIMIT_SEC = 600


SITE_CIRCUIT_TIME_LIMIT = f"qpm.{CIRCUIT_TIME_LIMIT_KEY}"


def circuit_time_limit(path=None, service=None):
	"""The runner time limit in seconds from the site file, or None for
	no limit."""
	return circuit_time_limit_and_source(path, service)[0]


def circuit_time_limit_and_source(path=None, service=None):
	if service is None:
		service = _this_service_entry()
	if CIRCUIT_TIME_LIMIT_KEY in service:
		source = f"services[{service.get('name')}].{CIRCUIT_TIME_LIMIT_KEY}"
		return _time_limit(service[CIRCUIT_TIME_LIMIT_KEY], source), source
	if path is None:
		path = os.environ.get(SITE_CONFIG_ENV, "").strip()
	value = None
	if path and os.path.exists(path):
		try:
			import yaml
			with open(path, "r", encoding="utf-8") as stream:
				data = yaml.safe_load(stream) or {}
		except Exception:
			# As for the completion-queue settings, a file this cannot
			# read leaves the defaults.
			data = {}
		qpm = data.get("qpm") if isinstance(data, dict) else None
		if isinstance(qpm, dict):
			value = qpm.get(CIRCUIT_TIME_LIMIT_KEY)
	if value is None:
		return DEFAULT_CIRCUIT_TIME_LIMIT_SEC, SITE_CIRCUIT_TIME_LIMIT
	return _time_limit(value, SITE_CIRCUIT_TIME_LIMIT), SITE_CIRCUIT_TIME_LIMIT


def _this_service_entry():
	if not os.environ.get(SERVICE_CONFIG_ENV, "").strip():
		return {}
	from util.mpi import this_service_entry
	return this_service_entry()


def _time_limit(value, source):
	if isinstance(value, bool):
		raise ValueError(f"invalid {source}: {value!r}")
	try:
		limit = float(value)
	except (TypeError, ValueError):
		raise ValueError(f"invalid {source}: {value!r}") from None
	if limit < 0 or limit != limit:
		raise ValueError(
			f"{source} must be a number of seconds, or 0 for no limit: "
			f"{value!r}")
	return limit or None


class UTIL_QRC:
	# max work queue size
	THREAD_STATE_FREE = 0
	THREAD_STATE_BUSY = 1

	def __init__(self, num_workers=8, num_worker_tasks=256, start=True):
		print_thread_stack_trace_to_logger(level='debug')
		self.shutdown_workers = False
		self.circuit_results_lock = threading.Lock()
		self.worker_pool_lock = threading.Lock()
		self.circuit_results = []
		self.push_info = {}
		self.module_util = None
		self.worker_pool = []
		self.worker_pool_rr = 0
		self.num_workers = num_workers
		self.num_worker_tasks = num_worker_tasks
		self.circuit_time_limit, self.circuit_time_limit_source = (
			circuit_time_limit_and_source())
		logging.debug(f'start = {start}')
		if start:
			self.launcher = svc_launcher.Launcher()
			for x in range(0, self.num_workers):
				with self.worker_pool_lock:
					runner = threading.Thread(target=self.runner, args=(x,))
					logging.debug(f"inserting {x} in the worker pool")
					self.worker_pool.append({
						'thread': runner,
						'active_tasks': [],
						'queue': queue.Queue(),
						'state': UTIL_QRC.THREAD_STATE_FREE
					})
					runner.daemon = True
					runner.start()

	def __del__(self):
		print_thread_stack_trace_to_logger(level='debug')
		self.shutdown_workers = True
		with self.worker_pool_lock:
			for v in self.worker_pool:
				v['queue'].put(None)

	def parse_task_result(self, stdout, circ, task_info):
		return self.parse_result(stdout)

	def cleanup_task(self, circ, task_info):
		pass

	def check_active_tasks(self, wid):
		complete = []
		for task_info in self.worker_pool[wid]['active_tasks']:
			try:
				#stdout, stderr, rc = task_info['launcher'].status(task_info['pid'])
				stdout, stderr, rc = self.launcher.status(task_info['pid'])
			except DEFwInProgress:
				self._enforce_time_limit(task_info)
				continue
			except Exception as e:
				raise e

			# at this point, it already has a return code!
			complete.append(task_info)
			try:
				self._finish_task(task_info, stdout, stderr, rc)
			except Exception:
				# The error belongs to this circuit. If it escaped,
				# this runner would die with its other tasks, and the
				# QPM would stop running circuits (openQSE/QFw#196).
				logging.critical(
					"finishing circuit "
					f"{task_info['circ'].get_cid()} failed:\n"
					f"{traceback.format_exc()}")

		for task_info in complete:
			self.worker_pool[wid]['active_tasks'].remove(task_info)

	def _enforce_time_limit(self, task_info):
		"""Kill a runner that has outlived the circuit time limit.

		The launcher then reports the exit, and the circuit fails like
		any other whose runner was killed.
		"""
		limit = self.circuit_time_limit
		if not limit or task_info.get('time_limit_hit'):
			return
		started = task_info.get('started')
		if started is None or time.monotonic() - started < limit:
			return
		pid = task_info['pid']
		# A launch on another host returns 0, and killpg(0) would
		# kill this process's own group.
		if not isinstance(pid, int) or pid <= 0:
			return
		task_info['time_limit_hit'] = limit
		logging.critical(
			f"circuit {task_info['circ'].get_cid()} has run for more "
			f"than {limit:g} s ({self.circuit_time_limit_source}); killing "
			f"its launcher, pid {pid}")
		try:
			# The launcher starts each runner in a session of its own,
			# so this reaches whatever it started on this host.
			os.killpg(pid, signal.SIGKILL)
		except ProcessLookupError:
			pass

	def _finish_task(self, task_info, stdout, stderr, rc):
		circ = task_info['circ']
		cid = circ.get_cid()
		qasm_file = task_info['qasm_file']

		error = None
		try:
			if rc == 0:
				try:
					output = self.parse_task_result(stdout, circ, task_info)
					circ.set_exec_done()
				except Exception as e:
					logging.critical(f"parse result failure = {e}")
					output = "{result: missing, exception: " + f"{e}" + "}"
					circ.set_fail()
					error = e
			else:
				stdout = stdout.decode('utf-8')
				stderr = stderr.decode('utf-8')
				res = stdout + '\n' + stderr
				limit = task_info.get('time_limit_hit')
				if limit:
					message = (
						f"circuit runner killed after {limit:g} s, the "
						f"QPM's circuit time limit "
						f"({self.circuit_time_limit_source})")
					res = message + '\n' + res
					error = DEFwExecutionError(message)
				else:
					error = DEFwExecutionError(
						f"circuit runner exited {rc}")
				output = "{result: " + f"{res}" + "}"
				circ.set_fail()
		finally:
			self.cleanup_task(circ, task_info)
			instrumentation.finish_backend_execution(
				task_info.get('telemetry'), circ, error=error)

		try:
			os.remove(qasm_file)
		except OSError:
			pass

		r = {
			'cid': cid,
			'result': output,
			'rc': rc,
			'launch_time': circ.launch_time,
			'creation_time': circ.creation_time,
			'exec_time': circ.exec_time,
			'completion_time': circ.completion_time,
			'resources_consumed_time': circ.resources_consumed_time,
			'cq_enqueue_time': time.time(),
			'cq_dequeue_time': -1
		}

		logging.defw_service(f"QPM task result: {r}")
		try:
			circ.free_resources(circ, result=r)
		finally:
			# The client gets its result even if the QPM's own
			# bookkeeping for it failed.
			self._push_or_store_result(r)

	def runner(self, my_id):
		# get the next available entry on the queue
		# if one is available run it
		# check on currently running tasks to see if any of them complete
		# Add completed tasks to the results dictionary
		super_affinity = sorted(os.sched_getaffinity(0))
		with self.worker_pool_lock:
			my_queue = self.worker_pool[my_id]['queue']
			# bind to core
			# We can enhance this more to avoid core 0 in every l3 cache
			# The processes a runner launches inherit its core. Choose from
			# the cores this process may use, which need not start at 0.
			bound_core = super_affinity[(my_id + 1) % len(super_affinity)]
			try:
				logging.debug(
					f'binding {threading.get_ident()} to '
					f'{bound_core} from  {os.sched_getaffinity(0)}')
				os.sched_setaffinity(0, {bound_core})
			except Exception as e:
				logging.critical(f'Failed to bind {threading.get_ident()} to {bound_core}')
				raise e

		while not self.shutdown_workers:
			# Poll only while launched tasks may finish. An idle runner
			# blocks until async_run() or shutdown() fills its queue.
			if self.worker_pool[my_id]['active_tasks']:
				timeout = 0.001
			else:
				timeout = None
			empty = False
			try:
				circ = my_queue.get(timeout=timeout)
				if circ is None:
					self.shutdown_workers = True
					continue
			except queue.Empty:
				empty = True

			self.check_active_tasks(my_id)

			if not empty:
				result = None
				try:
					task_info = self.run_circuit_async(circ)
				except Exception as e:
					logging.critical(
						f"Async circuit {circ.get_cid()} failed before launch: {e}")
					circ.set_fail()
					result = f"{type(e).__name__}: {e}"
					rc = -1
					pass

				if my_queue.qsize() < self.num_worker_tasks:
					with self.worker_pool_lock:
						self.worker_pool[my_id]['state'] = UTIL_QRC.THREAD_STATE_FREE

				if result:
					r = {
						'cid': circ.get_cid(),
						'result': result,
						'rc': rc,
						'launch_time': circ.launch_time,
						'creation_time': circ.creation_time,
						'exec_time': circ.exec_time,
						'completion_time': circ.completion_time,
						'resources_consumed_time': circ.resources_consumed_time,
						'cq_enqueue_time': time.time(),
						'cq_dequeue_time': -1
					}
					circ.free_resources(circ, result=r)
					self._push_or_store_result(r)
				else:
					self.worker_pool[my_id]['active_tasks'].append(task_info)

	def read_cq(self, cid=None):
		if cid:
			with self.circuit_results_lock:
				i = 0
				for e in self.circuit_results:
					if cid == e['cid']:
						r = self.circuit_results.pop(i)
						r['cq_dequeue_time'] = time.time()
						return r
					i += 1
		else:
			with self.circuit_results_lock:
				if len(self.circuit_results) > 0:
					r = self.circuit_results.pop(0)
					r['cq_dequeue_time'] = time.time()
					return r
		return None

	def _push_or_store_result(self, result):
		if self.push_info:
			event = Event(self.push_info['evtype'], result)
			try:
				delivered = self.push_info['class'].put(event)
			except Exception as e:
				logging.critical(
					"Failed to push event to client. "
					f"Exception encountered {e}")
				raise e
			if delivered is not False:
				return
		with self.circuit_results_lock:
			self.circuit_results.append(result)

	def peak_cq(self, cid=None):
		if cid:
			with self.circuit_results_lock:
				i = 0
				for e in self.circuit_results:
					if cid == e['cid']:
						return e
					i += 1
		else:
			with self.circuit_results_lock:
				if len(self.circuit_results) > 0:
					return self.circuit_results[0]
		return None

	def run_circuit_async(self, circ):
		cid = circ.get_cid()

		tmp_dir = cdefw_global.get_defw_tmp_dir()

		qasm_c = openqasm2_text(circ.info)
		qasm_file = os.path.join(tmp_dir, str(cid) + ".qasm")
		with open(qasm_file, 'w') as f:
			f.write(qasm_c)

		cmd = self.form_cmd(circ, qasm_file)
		# The run spans two visits by the worker: the launch here and the
		# completion check_active_tasks sees later, so the execution span is
		# carried in task_info rather than scoped to a block.
		execution = instrumentation.begin_backend_execution(
			circ, instrumentation.API_PATH_SIMULATOR,
			device=self._device_name(circ), backend_kind=self._backend_kind(circ))
		circ.set_launching()
		try:
			task_info = {}
			logging.debug(f"Running -- {cmd}")
			pid = self.launcher.launch(cmd)
			logging.debug(f"Running -- {cmd} -- with pid {pid}")
			circ.set_running()
		except Exception as e:
			instrumentation.finish_backend_execution(execution, circ, error=e)
			os.remove(qasm_file)
			logging.critical(f"Failed to launch {cmd}")
			raise e

		task_info['circ'] = circ
		task_info['qasm_file'] = qasm_file
		task_info['pid'] = pid
		task_info['started'] = time.monotonic()
		task_info['telemetry'] = execution

		return task_info

	def run_circuit(self, circ):
		cid = circ.get_cid()

		tmp_dir = cdefw_global.get_defw_tmp_dir()

		qasm_c = openqasm2_text(circ.info)
		qasm_file = os.path.join(tmp_dir, str(cid) + ".qasm")
		with open(qasm_file, 'w') as f:
			f.write(qasm_c)

		launcher = svc_launcher.Launcher()

		cmd = self.form_cmd(circ, qasm_file)
		execution = instrumentation.begin_backend_execution(
			circ, instrumentation.API_PATH_SIMULATOR,
			device=self._device_name(circ), backend_kind=self._backend_kind(circ))
		circ.set_launching()
		try:
			logging.debug(f"Running -- {cmd}")
			circ.set_running()
			output, error, rc = launcher.launch(cmd, wait=True)
			task_info = {'qasm_file': qasm_file}
			output = self.parse_task_result(output, circ, task_info)
			launcher.shutdown()
			logging.debug(f"Completed -- {cmd} -- returned {rc} -- {output} -- {error}")
		except Exception as e:
			instrumentation.finish_backend_execution(execution, circ, error=e)
			launcher.shutdown()
			self.cleanup_task(circ, {'qasm_file': qasm_file})
			os.remove(qasm_file)
			logging.critical(f"Failed to launch {cmd}")
			raise e

		self.cleanup_task(circ, {'qasm_file': qasm_file})
		os.remove(qasm_file)

		if rc == 0:
			circ.set_exec_done()
			instrumentation.finish_backend_execution(execution, circ)
			r = {
				'cid': cid,
				'result': output,
				'rc': rc,
				'launch_time': circ.launch_time,
				'creation_time': circ.creation_time,
				'exec_time': circ.exec_time,
				'completion_time': circ.completion_time,
				'resources_consumed_time': circ.resources_consumed_time,
				'cq_enqueue_time': time.time(),
				'cq_dequeue_time': -1
			}
			return r

		circ.set_fail()
		error_str = (
			f"Circuit failed with {rc}:{output.decode('utf8')}:"
			f"{error.decode('utf-8')}:"
			f"total run-time = {circ.exec_time - circ.completion_time}")
		logging.debug(error_str)
		failure = DEFwExecutionError(error_str)
		instrumentation.finish_backend_execution(execution, circ, error=failure)
		raise failure

	def _backend_kind(self, circ):
		info = getattr(circ, "info", None) or {}
		return info.get("qfw_backend")

	def _device_name(self, circ):
		# A simulator QPM has no device of its own; the device id the
		# deployment gave it, else the simulator's name, keeps its signals
		# apart from other services on a dashboard.
		return os.environ.get("QFW_QPU_DEVICE_ID") or self._backend_kind(circ)

	def sync_run(self, circ):
		return self.run_circuit(circ)

	# Round robin over the workers so that they are all busy
	def async_run(self, circ):
		cid = circ.get_cid()
		rr = self.worker_pool_rr
		self.worker_pool_rr += 1
		idx = rr % self.num_workers
		i = idx
		with self.worker_pool_lock:
			while True:
				worker = self.worker_pool[i]
				if worker['state'] == UTIL_QRC.THREAD_STATE_FREE and worker['queue'].qsize() < self.num_worker_tasks:
					worker['queue'].put(circ)
					if worker['queue'].qsize() >= self.num_worker_tasks:
						worker['state'] = UTIL_QRC.THREAD_STATE_BUSY
					return cid
				else:
					i = (i + 1) % self.num_workers
					if i == idx:
						break
		# if we get here then there is no more threads to handle this
		# request. Raise an exception and the circuit will be queued
		raise DEFwOutOfResources(f"No more resource to run {cid}")

	def register_event_notification(self, info):
		self.push_info = info

	def shutdown(self):
		logging.critical("shutdown called")
		self.launcher.shutdown()
		self.shutdown_workers = True
		# Wake the runners blocked on an empty queue.
		with self.worker_pool_lock:
			for v in self.worker_pool:
				v['queue'].put(None)
