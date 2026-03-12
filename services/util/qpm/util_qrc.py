from api_events import Event
from defw_agent_info import *
from defw_util import prformat, fg, bg
from defw import me
import logging, uuid, time, queue, threading, sys, os, io, contextlib
import importlib, yaml, psutil
from defw_exception import DEFwError, DEFwExists, DEFwExecutionError, DEFwInProgress, DEFwOutOfResources
import svc_launcher, cdefw_global
from defw_util import print_thread_stack_trace_to_logger

sys.path.append(os.path.split(os.path.abspath(__file__))[0])

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
		self.num_cores = psutil.cpu_count(logical=False)
		logging.debug(f'num_cores = {self.num_cores} start = {start}')
		if start:
			self.launcher = svc_launcher.Launcher()
			for x in range(0, self.num_workers):
				with self.worker_pool_lock:
					runner = threading.Thread(target=self.runner, args=(x,))
					logging.debug(f"inserting {x} in the worker pool")
					self.worker_pool.append({'thread': runner,
											 'active_tasks': [],
											 'queue': queue.Queue(),
											 'state': UTIL_QRC.THREAD_STATE_FREE})
					runner.daemon = True
					runner.start()

	def __del__(self):
		print_thread_stack_trace_to_logger(level='debug')
		self.shutdown_workers = True
		with self.worker_pool_lock:
			for v in self.worker_pool:
				v['queue'].put(None)

	def check_active_tasks(self, wid):
		complete = []
		for task_info in self.worker_pool[wid]['active_tasks']:
			try:
				#stdout, stderr, rc = task_info['launcher'].status(task_info['pid'])
				stdout, stderr, rc = self.launcher.status(task_info['pid'])
			except DEFwInProgress:
				continue
			except Exception as e:
				raise e

			# at this point, it already has a return code!
			complete.append(task_info)

			circ = task_info['circ']
			cid = circ.get_cid()
			qasm_file = task_info['qasm_file']

			if rc == 0:
				try:
					output = self.parse_result(stdout)
					circ.set_exec_done()
				except Exception as e:
					logging.critical(f"parse result failure = {e}")
					output = "{result: missing, exception: "+ f"{e}" + "}"
					circ.set_fail()
			else:
				stdout = stdout.decode('utf-8')
				stderr = stderr.decode('utf-8')
				res = stdout + '\n' + stderr
				output = "{result: "+ f"{res}" + "}"
				circ.set_fail()

			try:
				os.remove(qasm_file)
			except:
				pass

			r = {'cid': cid,
				 'result': output,
				 'rc': rc,
				 'launch_time': circ.launch_time,
				 'creation_time': circ.creation_time,
				 'exec_time': circ.exec_time,
				 'completion_time': circ.completion_time,
				 'resources_consumed_time': circ.resources_consumed_time,
				 'cq_enqueue_time': time.time(),
				 'cq_dequeue_time': -1 }

			circ.free_resources(circ)

			# push the result if push info were registered:
			if self.push_info:
				event = Event(self.push_info['evtype'], r)
				try:
					self.push_info['class'].put(event)
				except Exception as e:
					logging.critical(f"Failed to push event to client. Exception encountered {e}")
					raise e
			else:
				with self.circuit_results_lock:
					self.circuit_results.append(r)

		for task_info in complete:
			self.worker_pool[wid]['active_tasks'].remove(task_info)

	def runner(self, my_id):
		# get the next available entry on the queue
		# if one is available run it
		# check on currently running tasks to see if any of them complete
		# Add completed tasks to the results dictionary
		super_affinity = os.sched_getaffinity(0)
		with self.worker_pool_lock:
			my_queue = self.worker_pool[my_id]['queue']
			# bind to core
			# We can enhance this more to avoid core 0 in every l3 cache
			bound_core = (my_id + 1) % self.num_cores
			if not bound_core in super_affinity:
				tmp = bound_core + 1
				while tmp != bound_core:
					if tmp in super_affinity:
						bound_core = tmp
						break
					tmp  = (tmp + 1) % len(super_affinity)
			try:
				logging.debug(f'binding {threading.get_ident()} to ' \
							  f'{bound_core} from  {os.sched_getaffinity(0)}')
				os.sched_setaffinity(0, {bound_core})
			except Exception as e:
				logging.critical(f'Failed to bind {threading.get_ident()} to {bound_core}')
				raise e

		while not self.shutdown_workers:
			empty = False
			try:
				circ = my_queue.get(timeout = 0.001) # sleep
				if circ == None:
					self.shutdown_workers = True
					continue
			except queue.Empty:
				empty = True

			self.check_active_tasks(my_id)

			if not empty:
				result = None
				pid = -1
				try:
					task_info = self.run_circuit_async(circ)
				except Exception as e:
					result = e
					rc = -1
					pass

				if my_queue.qsize() < self.num_worker_tasks:
					with self.worker_pool_lock:
						self.worker_pool[my_id]['state'] = UTIL_QRC.THREAD_STATE_FREE

				if result:
					r = {'cid': circ.get_cid(),
						'result': result,
						'rc': rc,
						'launch_time': circ.launch_time,
						'creation_time': circ.creation_time,
						'exec_time': circ.exec_time,
						'completion_time': circ.completion_time,
						'resources_consumed_time': circ.resources_consumed_time,
						'cq_enqueue_time': time.time(),
						'cq_dequeue_time': -1}
					with self.circuit_results_lock:
						self.circuit_results.append(r)
						circ.free_resources(circ)
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

		qasm_c = circ.info["qasm"]
		qasm_file = os.path.join(tmp_dir, str(cid)+".qasm")
		with open(qasm_file, 'w') as f:
			f.write(qasm_c)

		circ.set_launching()
		cmd = self.form_cmd(circ, qasm_file)
		try:
			task_info = {}
			pid = self.launcher.launch(cmd)
			logging.debug(f"Running -- {cmd} -- with pid {pid}")
			circ.set_running()
		except Exception as e:
			os.remove(qasm_file)
			logging.critical(f"Failed to launch {cmd}")
			raise e

		task_info['circ'] = circ
		task_info['qasm_file'] = qasm_file
		task_info['pid'] = pid

		return task_info

	def run_circuit(self, circ):
		cid = circ.get_cid()

		tmp_dir = cdefw_global.get_defw_tmp_dir()

		qasm_c = circ.info["qasm"]
		qasm_file = os.path.join(tmp_dir, str(cid)+".qasm")
		with open(qasm_file, 'w') as f:
			f.write(qasm_c)

		circ.set_launching()
		launcher = svc_launcher.Launcher()

		cmd = self.form_cmd(circ, qasm_file)
		try:
			logging.debug(f"Running -- {cmd}")
			circ.set_running()
			output, error, rc = launcher.launch(cmd, wait=True)
			output = self.parse_result(output)
			launcher.shutdown()
			logging.debug(f"Completed -- {cmd} -- returned {rc} -- {output} -- {error}")
		except Exception as e:
			launcher.shutdown()
			os.remove(qasm_file)
			logging.critical(f"Failed to launch {cmd}")
			raise e

		os.remove(qasm_file)

		if rc == 0:
			circ.set_exec_done()
			r = {'cid': cid,
				 'result': output,
				 'rc': rc,
				 'launch_time': circ.launch_time,
				 'creation_time': circ.creation_time,
				 'exec_time': circ.exec_time,
				 'completion_time': circ.completion_time,
				 'resources_consumed_time': circ.resources_consumed_time,
				 'cq_enqueue_time': time.time(),
				 'cq_dequeue_time': -1 }
			return r

		circ.set_fail()
		error_str = f"Circuit failed with {rc}:{output.decode('utf8')}:" \
				f"{error.decode('utf-8')}:" \
				f"total run-time = {circ.exec_time - circ.completion_time}"
		logging.debug(error_str)
		raise DEFwExecutionError(error_str)

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
				if worker['state'] == UTIL_QRC.THREAD_STATE_FREE and \
				   worker['queue'].qsize() < self.num_worker_tasks:
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

