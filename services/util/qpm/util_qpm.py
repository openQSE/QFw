from defw_agent_info import *  # noqa: F401,F403
from api_events import BaseEventAPI
from defw_util import expand_host_list
from defw import me
import logging
import uuid
import time
import queue
import os
from defw_exception import (
	DEFwError,
	DEFwExecutionError,
	DEFwNotReady,
	DEFwInProgress,
	DEFwOutOfResources,
)
from .controller import (
	QPM_TASK_CANCELLED,
	QPM_TASK_FAILED,
	QPM_TASK_PENDING_CAPACITY,
	QPM_TASK_RESOURCES_CONSUMED,
	QPM_TASK_SUBMITTED,
	controller_config,
	get_target_controller,
)
from .admission import QPMAdmissionUnavailable, QPMAdmissionValidationError
from .util_circuit import Circuit, MAX_PPN
from .request import parse_execution_request
from statistics import mean, median, stdev

DIAGNOSTIC_BYPASS_ENV = "QFW_QPM_DIAGNOSTIC_BYPASS_ENABLED"
qpm_initialized = False
qpm_shutdown = False


class QPMEventDispatcher:
	def __init__(self, controller):
		self.controller = controller

	def put(self, event):
		return self.controller.dispatch_completion_event(event)


class UTIL_QPM:
	def __init__(self, qrc, max_ppn=MAX_PPN, start=True, target_id=None,
		     admission_threading_mode=None, scheduler_threading_mode=None,
		     controller_serialization_mode=None,
		     admission_context_factory=None):
		self.qrc = qrc
		self.max_ppn = max_ppn
		config = controller_config(
			qrc,
			target_id=target_id,
			admission_threading_mode=admission_threading_mode,
			scheduler_threading_mode=scheduler_threading_mode,
			serialization_mode=controller_serialization_mode,
		)
		self.controller = get_target_controller(
			config, max_ppn,
			admission_context_factory=admission_context_factory)
		self.circuits = self.controller.circuits
		self.oor_queue = self.controller.oor_queue
		if self.oor_queue is None:
			self.oor_queue = queue.Queue()
			self.controller.oor_queue = self.oor_queue
		self.circuit_results = self.controller.circuit_results
		self.free_hosts = self.controller.free_hosts
		if not self.controller.resources_initialized:
			self.setup_host_resources(max_ppn)
			self.controller.resources_initialized = True
		self.all_results = self.controller.all_results
		self.push_info = self.controller.push_info

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
		prepared_info = dict(info)
		hook_info = self.prepare_circuit(prepared_info)
		if hook_info is not None:
			prepared_info = hook_info
		request = parse_execution_request(prepared_info)
		runtime = self.controller.register_circuit(cid, request.context,
							   request.payload)
		self.circuits[cid] = Circuit(
			cid, request.payload, self.free_resources_and_oor)
		self.circuits[cid].set_ready()
		logging.debug(
			f"{cid} qtask {runtime.qtask_id} added to circuit database "
			f"in {time.time() - start}")
		return cid

	def prepare_circuit(self, info):
		return info

	def delete_circuit(self, cid, reservation_id=None, token=None):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		if reservation_id is not None:
			request = parse_execution_request(
				{}, reservation_id=reservation_id, token=token)
			self.require_managed_execution(request)

		with self.controller.lock:
			if cid not in self.circuits:
				return
			circ = self.circuits[cid]
			if circ.can_delete():
				del self.circuits[cid]
				self.controller.cleanup_circuit(cid)
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
			raise DEFwOutOfResources(
				f"hosts requested is more than available"
				f" Available resources = {np}:{num_hosts}:{self.free_hosts}")

		tmp_resources = {}
		consumed_res = {}
		itrnp = 0
		for host in self.free_hosts.keys():
			if np == 0:
				break
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
			raise DEFwOutOfResources(
				f"Not enough slots to run simulation"
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
		with self.controller.lock:
			res = circ.info['hosts']
			for host in res.keys():
				if host not in self.free_hosts:
					raise DEFwError(f"Circuit has untracked host: {host}")
				if res[host] + self.free_hosts[host] > self.max_ppn:
					raise DEFwError(
						"Returning more resources than originally had")
				self.free_hosts[host] += res[host]
			circ.set_done()
			cid = circ.get_cid()
			self.circuits.pop(cid, None)
			self.controller.cleanup_circuit(cid)

	def free_resources_and_oor(self, circ):
		self.free_resources(circ)
		# When resources are free, go through the queue and try
		# to consume circuits from that queue until you run out of
		# resources again.
		self.process_oor_queue()

	def common_run(self, cid):
		circuit = self.circuits[cid]
		with self.controller.lock:
			self.consume_resources(circuit)
			circuit.set_resources_consumed()
			self.controller.set_task_state(
				circuit.info["qtask_id"], QPM_TASK_RESOURCES_CONSUMED)
		logging.debug(f"Running {cid}\n{circuit.info}")
		self.prepare_provider_submission(circuit)
		return circuit

	def prepare_provider_submission(self, circuit):
		return circuit

	def submit_provider_sync(self, circuit):
		self.controller.set_task_state(circuit.info["qtask_id"], QPM_TASK_SUBMITTED)
		return self.qrc.sync_run(circuit)

	def submit_provider_async(self, circuit):
		self.controller.set_task_state(circuit.info["qtask_id"], QPM_TASK_SUBMITTED)
		return self.qrc.async_run(circuit)

	def complete_provider_submission(self, circuit, result=None):
		self.controller.record_result(circuit.info["qtask_id"], result)

	def fail_provider_submission(self, circuit, error):
		self.controller.set_task_state(circuit.info["qtask_id"], QPM_TASK_FAILED)

	def cancel_provider_submission(self, cid, reason=None):
		runtime = self.controller.task_for_cid(cid)
		if runtime is None:
			return None
		self.controller.set_task_state(runtime.qtask_id, QPM_TASK_CANCELLED)
		return runtime

	def sync_run(self, info, common_run=None, reservation_id=None, token=None,
				 run_context=None, timeout=None, cancel_on_timeout=False,
				 **request_metadata):
		if not common_run:
			common_run = self.common_run
		else:
			self.common_run = common_run

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		request = parse_execution_request(
			info,
			reservation_id=reservation_id,
			token=token,
			run_context=run_context,
			timeout=timeout,
			cancel_on_timeout=cancel_on_timeout,
			**request_metadata,
		)
		self.require_managed_execution(request)
		return self._sync_run_request(request, common_run)

	def diagnostic_sync_run(self, info, token=None, reason=None,
				**request_metadata):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		request = parse_execution_request(
			info,
			token=token,
			**request_metadata,
		)
		self.require_diagnostic_bypass(
			request, operation="diagnostic_sync_run", reason=reason)
		return self._sync_run_request(request, self.common_run)

	def _sync_run_request(self, request, common_run):
		circuit = None
		try:
			cid = self.create_circuit(request.payload)
			circuit = common_run(cid)
			result = self.submit_provider_sync(circuit)
			self.complete_provider_submission(circuit, result=result)
		except Exception as e:
			if circuit is not None:
				self.fail_provider_submission(circuit, e)
				if "hosts" in circuit.info:
					self.free_resources(circuit)
			raise e
		if "hosts" in circuit.info:
			self.free_resources(circuit)
		logging.debug(f"circuit {circuit.get_cid()} completed with output {result}")
		return result

	def require_managed_execution(self, request):
		if request.context.reservation_id is not None:
			try:
				self.controller.validate_reservation_for_context(
					request.context)
			except (QPMAdmissionUnavailable,
				QPMAdmissionValidationError) as error:
				raise DEFwExecutionError(str(error))
			return
		raise DEFwExecutionError(
			"reservation_id is required for resource-affecting QPM execution")

	def require_diagnostic_bypass(self, request, operation, reason=None):
		if not diagnostic_bypass_enabled():
			raise DEFwExecutionError(
				"diagnostic bypass execution is disabled")
		self.controller.record_diagnostic_bypass(
			operation, request.context, reason=reason)

	def async_run_oor(self, cid, common_run=None):
		if not common_run:
			common_run = self.common_run
		else:
			self.common_run = common_run

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		circuit = None
		try:
			circuit = common_run(cid)
			self.submit_provider_async(circuit)
		except DEFwOutOfResources as e:
			# queue circuit on a local out of resources queue
			runtime = self.controller.task_for_cid(cid)
			if runtime is not None:
				self.controller.set_task_state(
					runtime.qtask_id, QPM_TASK_PENDING_CAPACITY)
			self.oor_queue.put(cid)
			raise e
		except Exception as e:
			if circuit is not None:
				self.fail_provider_submission(circuit, e)
				if "hosts" in circuit.info:
					self.free_resources(circuit)
			self.process_oor_queue()
			raise e

	def async_run(self, info, common_run=None, reservation_id=None, token=None,
				  run_context=None, timeout=None, cancel_on_timeout=False,
				  **request_metadata):
		if not common_run:
			common_run = self.common_run
		else:
			self.common_run = common_run

		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		request = parse_execution_request(
			info,
			reservation_id=reservation_id,
			token=token,
			run_context=run_context,
			timeout=timeout,
			cancel_on_timeout=cancel_on_timeout,
			**request_metadata,
		)
		self.require_managed_execution(request)
		return self._async_run_request(request, common_run)

	def diagnostic_async_run(self, info, token=None, reason=None,
				 **request_metadata):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		request = parse_execution_request(
			info,
			token=token,
			**request_metadata,
		)
		self.require_diagnostic_bypass(
			request, operation="diagnostic_async_run", reason=reason)
		return self._async_run_request(request, self.common_run)

	def _async_run_request(self, request, common_run):
		cid = None
		circuit = None
		try:
			cid = self.create_circuit(request.payload)
			circuit = common_run(cid)
			self.submit_provider_async(circuit)
		except DEFwOutOfResources as e:
			if cid is None:
				raise e
			# queue circuit on a local out of resources queue
			runtime = self.controller.task_for_cid(cid)
			if runtime is not None:
				self.controller.set_task_state(
					runtime.qtask_id, QPM_TASK_PENDING_CAPACITY)
			self.oor_queue.put(cid)
		except Exception as e:
			if circuit is not None:
				self.fail_provider_submission(circuit, e)
				if "hosts" in circuit.info:
					self.free_resources(circuit)
			self.process_oor_queue()
			raise e

		return cid

	def read_cq(self, cid=None, reservation_id=None, token=None):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		r = self.qrc.read_cq(cid)

		if not r:
			if cid:
				raise DEFwInProgress(f"{cid} still in progress")
			else:
				raise DEFwInProgress("No ready QTs")

		self.all_results.append(r)
		cid = r.get("cid") if isinstance(r, dict) else None
		runtime = self.controller.task_for_cid(cid)
		if runtime is not None:
			self.controller.record_result(runtime.qtask_id, r)
		return r

	def peek_cq(self, cid=None, reservation_id=None, token=None):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		r = self.qrc.peak_cq()

		if not r:
			if cid:
				raise DEFwInProgress(f"{cid} still in progress")
			else:
				raise DEFwInProgress("No ready QTs")

		return r

	def register_event_notification(self, ep, evtype, class_id, token=None,
					reservation_id=None, filters=None):
		if reservation_id is not None:
			request = parse_execution_request(
				{}, reservation_id=reservation_id, token=token)
			self.require_managed_execution(request)
		push_info = {
			"class": BaseEventAPI(class_id=class_id, target=ep),
			"evtype": evtype,
			"class_id": class_id,
			"target": ep,
			"reservation_id": reservation_id,
			"filters": dict(filters or {}),
		}
		result = self.controller.register_event_endpoint(push_info)
		self._ensure_qrc_event_dispatcher(evtype)
		return result

	def _ensure_qrc_event_dispatcher(self, evtype):
		if self.qrc is None:
			return
		with self.controller.lock:
			dispatcher = self.controller.callback_endpoints.get(
				"completion-event-dispatcher")
			if dispatcher is None:
				dispatcher = QPMEventDispatcher(self.controller)
				self.controller.callback_endpoints[
					"completion-event-dispatcher"] = dispatcher
			self.controller.push_info.update({
				"class": dispatcher,
				"evtype": evtype,
				"class_id": "qpm-completion-event-dispatcher",
				"target": "qpm-controller",
			})
			push_info = dict(self.controller.push_info)
		self.qrc.register_event_notification(push_info)

	def is_ready(self):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		return True

	def query_helper(self, type_bits, caps_bits, svc_name, svc_desc,
					 properties=None):
		from api_qpm import QPMType, QPMCapability
		from defw_agent_info import get_bit_list, get_bit_desc, Capability, DEFwServiceInfo
		properties = dict(properties or {})
		properties.setdefault("controller", self.controller_telemetry())
		t = get_bit_list(type_bits, QPMType)
		c = get_bit_list(caps_bits, QPMCapability)
		cap = Capability(type_bits, caps_bits, get_bit_desc(t, c))
		info = DEFwServiceInfo(
			svc_name, svc_desc,
			self.__class__.__name__,
			self.__class__.__module__,
			cap, -1,
			properties=properties)
		return info

	def controller_telemetry(self):
		telemetry = self.controller.telemetry()
		telemetry["diagnostic_bypass_enabled"] = diagnostic_bypass_enabled()
		return telemetry

	def configure_device_profile(self, profile=None, **overrides):
		device_profile = dict(profile or {})
		device_profile.update(overrides)
		return self.controller.configure_device_profile(device_profile)

	def get_admission_policy(self, token=None):
		return self.controller.get_admission_policy()

	def set_admission_policy(self, policy, token=None):
		return self.controller.set_admission_policy(policy)

	def get_capacity_model(self, token=None):
		return self.controller.get_capacity_model()

	def set_capacity_model(self, capacity_model, token=None):
		return self.controller.set_capacity_model(capacity_model)

	def get_estimator_policy(self, token=None):
		return self.controller.get_estimator_policy()

	def set_estimator_policy(self, estimator, token=None):
		return self.controller.set_estimator_policy(estimator)

	def evaluate(self, request, token=None):
		return self.controller.evaluate_reservation(request, token=token)

	def reserve(self, request=None, token=None, *args, **kwargs):
		if not isinstance(request, dict):
			logging.debug(f"{token} reserved the {request}")
			return None
		return self.controller.reserve_admission(request, token=token)

	def renew(self, reservation_id, request=None, token=None):
		return self.controller.renew_admission(
			reservation_id, request=request, token=token)

	def release(self, reservation_id=None, token=None, reason=None,
		    services=None):
		if reservation_id is not None and not isinstance(
				reservation_id, (list, tuple, set)):
			return self.controller.release_admission(
				reservation_id, reason_code=reason or 0, token=token)
		return self.release_service(services=services or reservation_id)

	def cancel(self, reservation_id, reason=None, token=None):
		return self.controller.cancel_admission(
			reservation_id, reason_code=reason or 0, token=token)

	def get_reservation(self, reservation_id, token=None):
		return self.controller.get_admission_reservation(
			reservation_id, token=token)

	def list_reservations(self, filters=None, token=None):
		return self.controller.list_admission_reservations(
			filters=filters, token=token)

	def release_service(self, services=None):
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
		except Exception:
			pass

		self.shutdown_provider()
		#ss = threading.Thread(target=self.schedule_shutdown, args=())
		#ss.start()

	def test(self):
		return "****UTIL QPM Test Successful****"

	def shutdown_provider(self):
		if self.qrc:
			self.qrc.shutdown()
			self.qrc = None


def diagnostic_bypass_enabled():
	value = os.environ.get(DIAGNOSTIC_BYPASS_ENV, "no").strip().lower()
	return value in ("1", "true", "yes", "on", "y")
