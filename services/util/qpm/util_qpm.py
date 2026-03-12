from defw_agent_info import *
from api_events import BaseEventAPI
from defw_util import expand_host_list, round_half_up, round_to_nearest_power_of_two
from defw import me
import logging, uuid, time, queue, threading, logging, yaml
from defw_exception import DEFwError, DEFwNotReady, DEFwInProgress
import os
from .util_circuit import Circuit, MAX_PPN
from statistics import mean, median, stdev

qpm_initialized = False
qpm_shutdown = False

class UTIL_QPM:
	def __init__(self, qrc, max_ppn=MAX_PPN, start=True):
		self.circuits = {}
		#self.runner_queue = queue.Queue()
		self.oor_queue = queue.Queue()
		self.circuit_results = []
		self.qrc = qrc
		self.free_hosts = {}
		self.max_ppn = max_ppn
		self.setup_host_resources(max_ppn)
		self.all_results = []
		self.push_info = {}

	def setup_host_resources(self, max_ppn):
		hl = expand_host_list(os.environ['QFW_QPM_ASSIGNED_HOSTS'])
		for h in hl:
			comp = h.split(':')
			if len(comp) == 1:
				self.free_hosts[comp[0]] = max_ppn
			elif len(comp) == 2:
				self.free_hosts[comp[0]] = int(comp[1])

	def create_circuit(self, info):
		start = time.time()

		cid = str(uuid.uuid4())
		self.circuits[cid] = Circuit(cid, info, self.free_resources_and_oor)
		self.circuits[cid].set_ready()
		logging.debug(f"{cid} added to circuit database in {time.time() - start}")
		return cid

	def delete_circuit(self, cid):
		global qpm_initialized

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		if cid not in self.circuits:
			return
		circ = self.circuits[cid]
		if circ.can_delete():
			del self.circuits[cid]
		else:
			circ.set_deletion()

	def consume_resources(self, circ):
		info = circ.info
		np = info['np']
		num_hosts = int(np / self.max_ppn)
		if not num_hosts:
			num_hosts = 1

		# determine if we have enough hosts to run this circuit
		# If the number of hosts required is more than the total number
		# of hosts then we can't run the circuit.
		if num_hosts > len(self.free_hosts.keys()):
			raise DEFwOutOfResources(f"hosts requested is more than available" \
									 f" Available resources = {np}:{num_hosts}:{self.free_hosts}")

		tmp_resources = {}
		consumed_res = {}
		itrnp = 0
		for host in self.free_hosts.keys():
			if np == 0:
				break;
			tmp_resources[host] = self.free_hosts[host]
			if self.free_hosts[host] >= np:
				self.free_hosts[host] = self.free_hosts[host] - np
				consumed_res[host] = np
				itrnp += np
				np = 0
			elif self.free_hosts[host] < np and self.free_hosts[host] != 0:
				np -= self.free_hosts[host]
				itrnp += self.free_hosts[host]
				consumed_res[host] = self.free_hosts[host]
				self.free_hosts[host] = 0
		if np != 0:
			# restore whatever was consumed
			for k, v in tmp_resources.items():
				self.free_hosts[k] = v
			raise DEFwOutOfResources(f"Not enough slots to run simulation" \
									 f" Available resources = {np}:{num_hosts}:{self.free_hosts}")

		circ.info['hosts'] = consumed_res
		logging.debug(f"Circuit consumed: {consumed_res}")

	def process_oor_queue(self):
		while True:
			if self.oor_queue.empty():
				break
			try:
				# now that we have the resources for the circuit secured
				# pop that entry off the queue.
				cid = self.oor_queue.get(block=False)
				self.async_run_oor(cid, self.common_run)
			except DEFwOutOfResources:
				break

	def free_resources(self, circ):
		res = circ.info['hosts']
		for host in res.keys():
			if host not in self.free_hosts:
				raise DEFwError(f"Circuit has untracked host: {host}")
			if res[host] + self.free_hosts[host] > self.max_ppn:
				raise DEFwError("Returning more resources than originally had")
			self.free_hosts[host] += res[host]
		circ.set_done()
		cid = circ.get_cid()
		self.delete_circuit(cid)

	def free_resources_and_oor(self, circ):
		self.free_resources(circ)
		# When resources are free, go through the queue and try
		# to consume circuits from that queue until you run out of
		# resources again.
		self.process_oor_queue()

	def common_run(self, cid):
		circuit = self.circuits[cid]
		self.consume_resources(circuit)
		circuit.set_resources_consumed()
		logging.debug(f"Running {cid}\n{circuit.info}")
		return circuit

	def sync_run(self, info, common_run=None):
		global qpm_initialized

		if not common_run:
			common_run = self.common_run
		else:
			self.common_run = common_run

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		try:
			cid = self.create_circuit(info)
			circuit = common_run(cid)
			result = self.qrc.sync_run(circuit)
		except Exception as e:
			raise e
		self.free_resources(circuit)
		logging.debug(f"circuit {circuit.get_cid()} completed with output {result}")
		return result

	def async_run_oor(self, cid, common_run=None):
		global qpm_initialized

		if not common_run:
			common_run = self.common_run
		else:
			self.common_run = common_run

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		try:
			circuit = common_run(cid)
			self.qrc.async_run(circuit)
		except DEFwOutOfResources as e:
			# queue circuit on a local out of resources queue
			self.oor_queue.put(cid)
			raise e
		except Exception as e:
			self.process_oor_queue()
			raise e

	def async_run(self, info, common_run=None):
		global qpm_initialized

		if not common_run:
			common_run = self.common_run
		else:
			self.common_run = common_run

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		try:
			cid = self.create_circuit(info)
			circuit = common_run(cid)
			self.qrc.async_run(circuit)
		except DEFwOutOfResources:
			# queue circuit on a local out of resources queue
			self.oor_queue.put(cid)
		except Exception as e:
			self.process_oor_queue()
			raise e

		return cid

	def read_cq(self, cid=None):
		global qpm_initialized

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		r = self.qrc.read_cq(cid)

		if not r:
			if cid:
				raise DEFwInProgress(f"{cid} still in progress")
			else:
				raise DEFwInProgress("No ready QTs")

		self.all_results.append(r)
		return r

	def peek_cq(self, cid=None):
		global qpm_initialized

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		r = self.qrc.peak_cq()

		if not r:
			if cid:
				raise DEFwInProgress(f"{cid} still in progress")
			else:
				raise DEFwInProgress("No ready QTs")

		return r

	def register_event_notification(self, ep, evtype, class_id):
		self.push_info['class'] = BaseEventAPI(class_id=class_id, target=ep)
		self.push_info['evtype'] = evtype
		# keeping the below for debugging purposes
		self.push_info['class_id'] = class_id
		self.push_info['target'] = ep
		self.qrc.register_event_notification(self.push_info)

	def is_ready(self):
		global qpm_initialized

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		return True

	def query_helper(self, type_bits, caps_bits, svc_name, svc_desc):
		from api_qpm import QPMType, QPMCapability
		from defw_agent_info import get_bit_list, get_bit_desc, \
									Capability, DEFwServiceInfo
		t = get_bit_list(type_bits, QPMType)
		c = get_bit_list(caps_bits, QPMCapability)
		cap = Capability(type_bits, caps_bits, get_bit_desc(t, c))
		info = DEFwServiceInfo(svc_name, svc_desc,
							   self.__class__.__name__,
							   self.__class__.__module__,
							   cap, -1)
		return info

	def reserve(self, svc, client_ep, *args, **kwargs):
		logging.debug(f"{client_ep} reserved the {svc}")

	def release(self, services=None):
		global qpm_shutdown

		qpm_shutdown = True
		if self.qrc:
			self.qrc.shutdown()
			self.qrc = None
		pass

	def schedule_shutdown(self, timeout=5):
		logging.debug(f"Shutting down in {timeout} seconds")
		time.sleep(timeout)
		me.exit()

	def compute_stats(self, data, label):
		logging.critical(f"Statistical Analysis for {label}:")
		logging.critical(f"Count: {len(data)}")
		logging.critical(f"Mean: {mean(data):.6f} seconds")
		logging.critical(f"Median: {median(data):.6f} seconds")
		logging.critical(f"Standard Deviation: {stdev(data):.6f} seconds" if len(data) > 1 else "N/A")
		logging.critical(f"Min: {min(data):.6f} seconds")
		logging.critical(f"Max: {max(data):.6f} seconds")

	def shutdown(self):
		logging.debug("Scheduling QPM Shutdown")
		create_launch = []
		launch_running = []
		exec_completion = []
		for r in self.all_results:
			create_launch.append(r['launch_time'] - r['creation_time'])
			launch_running.append(r['exec_time'] - r['launch_time'])
			exec_completion.append(r['completion_time'] - r['exec_time'])

		try:
			self.compute_stats(create_launch, 'create->launch')
			self.compute_stats(launch_running, 'launch->running')
			self.compute_stats(exec_completion, 'exec->completion')
		except:
			pass

		if self.qrc:
			self.qrc.shutdown()
			self.qrc = None
		#ss = threading.Thread(target=self.schedule_shutdown, args=())
		#ss.start()

	def test(self):
		return "****UTIL QPM Test Successful****"

