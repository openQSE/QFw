from defw_agent_info import *
from defw_util import prformat, fg, bg
from defw import me
import logging, uuid, time, queue, threading, sys, os, io, contextlib
import importlib, yaml, copy, subprocess, traceback
from defw_exception import DEFwExecutionError, DEFwInProgress
from util.qpm.util_qrc import UTIL_QRC

sys.path.append(os.path.split(os.path.abspath(__file__))[0])

# Custom loader to force all keys to be strings
class StringKeyLoader(yaml.SafeLoader):
	pass

def string_key_constructor(loader, node):
	return loader.construct_scalar(node)

StringKeyLoader.add_constructor("tag:yaml.org,2002:int", string_key_constructor)

class QRC(UTIL_QRC):
	def __init__(self, start=True):
		super().__init__(start=start)

	def check_active_tasks(self, wid):
		complete = []
		for task_info in self.worker_pool[wid]['active_tasks']:
			try:
				stdout, stderr, rc = task_info['launcher'].status(task_info['pid'])
			except DEFwInProgress:
				continue
			except Exception as e:
				raise e
			complete.append(task_info)
			circ = task_info['circ']
			cid = circ.get_cid()
			qasm_file = task_info['qasm_file']
			if not rc:
				try:
					output_file = qasm_file+".result"
					with open(output_file, 'r') as f:
						output = f.read()
						output = yaml.load(output, Loader=StringKeyLoader)
						#output = yaml.safe_load(output)
					os.remove(output_file)
					circ.set_exec_done()
				except Exception as e:
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
			with self.circuit_results_lock:
				self.circuit_results.append(r)

		for task_info in complete:
			self.worker_pool[wid]['active_tasks'].remove(task_info)

	def form_cmd(self, circ, qasm_file):
		import shutil

		info = circ.info
		circuit_runner = shutil.which(info['qfw_backend'])

		if not circuit_runner:
			raise DEFwExecutionError("Couldn't find circuit_runner. Check paths")

		cmd = f'{circuit_runner} -u {info["vqpu_url"]} ' \
			  f'-q {qasm_file} -b {info["num_qubits"]} -s {info["num_shots"]}'

		return cmd

	def test(self):
		return "****Testing the QB QRC****"
