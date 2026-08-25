from defw_agent_info import *  # noqa: F401,F403
from api_events import BaseEventAPI
from defw_util import expand_host_list
from defw import me
import logging
import uuid
import time
import queue
import os
import threading
from defw_exception import (
	DEFwError,
	DEFwExecutionError,
	DEFwNotReady,
	DEFwOutOfResources,
)
from .controller import (
	QPM_TASK_PENDING_CAPACITY,
	QPM_TASK_QUEUED,
	QPM_TASK_RESOURCES_CONSUMED,
	QPM_TASK_SELECTED,
	QPM_TASK_TERMINAL_STATES,
	controller_config,
	get_target_controller,
)
from .admission import (
	QPMAdmissionPendingCapacity,
	QPMAdmissionUnavailable,
	QPMAdmissionValidationError,
)
from .scheduler import QPMSchedulerError, QPMSchedulerUnavailable
from .util_circuit import Circuit, CircuitStates, MAX_PPN
from .request import parse_execution_request
from statistics import mean, median, stdev

QPM_SERVICE_TYPE = "qfw.qpm"
MANAGED_SUBMISSION_FAILURE_REASONS = (
	"credential-binding-failed",
	"scheduler-submission-failed",
	"provider-submission-failed",
)
QPM_CATEGORY_API_BINDINGS = (
	("execution", "api_qpm_execution", "QPMExecution"),
	("admission", "api_qpm_admission_control", "QPMAdmissionControl"),
	("admission-policy", "api_qpm_admission_policy_config",
	 "QPMAdmissionPolicyConfig"),
	("scheduler", "api_qpm_scheduler_control", "QPMSchedulerControl"),
	("telemetry", "api_qpm_telemetry", "QPMTelemetry"),
	("control", "api_qpm_control", "QPMControl"),
)
qpm_initialized = False
qpm_shutdown = False


class QPMEventDispatcher:
	def __init__(self, controller):
		self.controller = controller

	def put(self, event):
		return self.controller.dispatch_completion_event(event)


def _qpm_api_bindings(service_module, service_class):
	bindings = []
	for binding_name, client_module, client_class in QPM_CATEGORY_API_BINDINGS:
		bindings.append({
			"binding_name": binding_name,
			"client_module": client_module,
			"client_class": client_class,
			"service_module": service_module,
			"service_class": service_class,
			"version": 1,
		})
	return bindings


def _qpm_service_id(svc_name, service_module, provider, properties):
	device_id = properties.get("device_id") or properties.get("target_id")
	if device_id:
		return f"qpm:{provider or svc_name}:{device_id}"
	return f"qpm:{provider or service_module}:{svc_name}"


def _qpm_selector(properties, svc_name, provider):
	selector = dict(properties.get("selector") or {})
	resources = _metadata_list(selector.get("resources"))
	aliases = _metadata_list(selector.get("aliases"))
	device_id = properties.get("device_id") or properties.get("target_id")
	_add_metadata_value(resources, device_id)
	_add_metadata_value(resources, properties.get("resource_id"))
	_add_qpm_qubit_resource(resources, provider, properties.get("num_qubits"))
	if not resources:
		_add_metadata_value(resources, svc_name)
	_add_metadata_value(aliases, provider)
	_add_metadata_value(aliases, svc_name)
	selector["resources"] = resources
	selector["aliases"] = aliases
	selector.setdefault("name", device_id or provider or svc_name)
	return selector


def _qpm_type_bit_enabled(type_bits, bit):
	bits = _int_bits(type_bits)
	if bits is None:
		return False
	return bool(bits & int(bit))


def _int_bits(value):
	try:
		return int(value)
	except (TypeError, ValueError):
		return None


def _metadata_list(value):
	if value is None:
		return []
	if isinstance(value, (list, tuple, set)):
		return [str(item) for item in value if item is not None]
	return [str(value)]


def _add_metadata_value(items, value):
	if value is None:
		return
	value = str(value)
	if value and value not in items:
		items.append(value)


def _add_qpm_qubit_resource(resources, provider, num_qubits):
	if provider is None or num_qubits is None:
		return
	try:
		num_qubits = int(num_qubits)
	except (TypeError, ValueError):
		return
	_add_metadata_value(resources, f"{str(provider).upper()}-{num_qubits}q")


class UTIL_QPM:
	def __init__(self, qrc, max_ppn=MAX_PPN, start=True, target_id=None,
		     admission_threading_mode=None, scheduler_threading_mode=None,
		     controller_serialization_mode=None,
		     admission_context_factory=None,
		     scheduler_context_factory=None):
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
			admission_context_factory=admission_context_factory,
			scheduler_context_factory=scheduler_context_factory)
		self.controller.set_provider_canceller(
			getattr(qrc, "cancel", None) if qrc is not None else None)
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
		self._ensure_qrc_event_dispatcher("completion")
		self.controller.start_completion_purge_worker()

	def setup_host_resources(self, max_ppn):
		assigned_hosts = os.environ.get('QFW_QPM_ASSIGNED_HOSTS')
		hl = (expand_host_list(assigned_hosts) if assigned_hosts
		      else [os.uname().nodename])
		for h in hl:
			comp = h.split(':')
			if len(comp) == 1:
				self.free_hosts[comp[0]] = max_ppn
			elif len(comp) == 2:
				self.free_hosts[comp[0]] = int(comp[1])

	def create_circuit(self, info, request=None):
		start = time.time()

		cid = str(uuid.uuid4())
		prepared_info = dict(request.payload if request is not None else info)
		hook_info = self.prepare_circuit(prepared_info)
		if hook_info is not None:
			prepared_info = hook_info
		if request is None:
			request = parse_execution_request(prepared_info)
			request_context = request.context
			request_payload = request.payload
		else:
			request_context = request.context
			request_payload = prepared_info
		with self.controller.lock:
			runtime = self.controller.register_circuit(
				cid, request_context, request_payload)
			self.circuits[cid] = Circuit(
				cid, request_payload, self.free_resources_and_oor)
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
		error = self.controller.task_reservation_error(
			cid=cid, reservation_id=reservation_id,
			require_reservation=True)
		if error is not None:
			return error

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
		self.controller.retry_pending_capacity()
		while True:
			if not self._prune_oor_queue():
				break
			try:
				cid = self._next_oor_cid_without_scheduler_task()
				if cid is not None:
					self.async_run_oor(cid)
					self._remove_oor_cid(cid)
				else:
					status = self.dispatch_ready_qtask()
					if status is None:
						break
					self._remove_oor_cid(status.get("cid"))
			except DEFwOutOfResources:
				break

	def _prune_oor_queue(self):
		active = []
		while not self.oor_queue.empty():
			cid = self.oor_queue.get(block=False)
			runtime = self.controller.task_for_cid(cid)
			if (runtime is not None and
					runtime.state not in QPM_TASK_TERMINAL_STATES):
				active.append(cid)
		for cid in active:
			self.oor_queue.put(cid)
		return active

	def _remove_oor_cid(self, dispatched_cid):
		if dispatched_cid is None:
			return
		pending = []
		removed = False
		while not self.oor_queue.empty():
			cid = self.oor_queue.get(block=False)
			if not removed and cid == dispatched_cid:
				removed = True
				continue
			pending.append(cid)
		for cid in pending:
			self.oor_queue.put(cid)

	def _next_oor_cid_without_scheduler_task(self):
		pending = []
		selected = None
		while not self.oor_queue.empty():
			cid = self.oor_queue.get(block=False)
			pending.append(cid)
			runtime = self.controller.task_for_cid(cid)
			if (selected is None and runtime is not None and
					runtime.scheduler_task_id is None):
				selected = cid
		for cid in pending:
			self.oor_queue.put(cid)
		return selected

	def free_resources(self, circ, result=None):
		self.finalize_provider_task(circ, result=result)
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

	def free_resources_and_oor(self, circ, result=None):
		self.free_resources(circ, result=result)
		# When resources are free, go through the queue and try
		# to consume circuits from that queue until you run out of
		# resources again.
		self.process_oor_queue()

	def _prepare_run_circuit(self, cid, require_selected_cid=False):
		with self.controller.lock:
			circuit = self.circuits.get(cid)
		if circuit is None:
			raise DEFwExecutionError(
				f"qtask circuit record is no longer active: cid={cid}")
		try:
			if (circuit.info["qtask_id"]
					not in self.controller.capacity_holds):
				self.controller.authorize_capacity_hold(circuit)
			self.controller.submit_qtask_to_scheduler(circuit)
			selected_runtime = self.controller.select_qtask_for_dispatch()
			if selected_runtime is None:
				raise DEFwOutOfResources(
					"scheduler has no dispatch slot available")
			if require_selected_cid and selected_runtime.cid != cid:
				raise DEFwOutOfResources(
					"scheduler selected earlier queued work")
			circuit = self.circuits[selected_runtime.cid]
		except QPMAdmissionPendingCapacity as error:
			raise DEFwOutOfResources(str(error))
		except (QPMAdmissionUnavailable,
			QPMAdmissionValidationError,
			QPMSchedulerUnavailable,
			QPMSchedulerError) as error:
			raise DEFwExecutionError(str(error))
		with self.controller.lock:
			self.consume_resources(circuit)
			circuit.set_resources_consumed()
			self.controller.set_task_state(
				circuit.info["qtask_id"], QPM_TASK_RESOURCES_CONSUMED)
		logging.debug(f"Running {cid}\n{circuit.info}")
		if circuit.info.get("reservation_id") is not None:
			self.controller.attach_provider_credential(circuit)
		self.prepare_provider_submission(circuit)
		return circuit

	def prepare_provider_submission(self, circuit):
		return circuit

	def submit_provider_sync(self, circuit):
		runtime = self.controller.start_provider_submission(circuit)
		if runtime is None:
			raise DEFwOutOfResources("qtask is already submitted")
		return self.qrc.sync_run(circuit)

	def submit_provider_async(self, circuit, return_status=False):
		runtime = self.controller.start_provider_submission(circuit)
		if runtime is None:
			if return_status:
				return self.controller.task_status_for_cid(
					circuit.get_cid(),
					reservation_id=circuit.info.get("reservation_id"))
			return None
		response = (
			self.controller.task_status_for_cid(
				circuit.get_cid(),
				reservation_id=circuit.info.get("reservation_id"))
			if return_status else None)
		provider_handle = self.qrc.async_run(circuit)
		if provider_handle is not None:
			runtime = self.controller.task_for_cid(circuit.get_cid())
			if runtime is not None:
				self.controller.bind_provider_handle(
					runtime.qtask_id, provider_handle)
				if return_status:
					response["provider_handle"] = provider_handle
		if return_status:
			return response
		return provider_handle

	def dispatch_ready_qtask(self):
		circuit = None
		try:
			runtime = self.controller.select_qtask_for_dispatch()
			if runtime is None:
				return None
			circuit = self._prepare_run_circuit(
				runtime.cid, require_selected_cid=True)
			return self.submit_provider_async(circuit, return_status=True)
		except DEFwOutOfResources:
			if circuit is not None:
				self.defer_local_retry(circuit.get_cid())
			raise
		except Exception as e:
			if circuit is not None:
				self.fail_provider_submission(circuit, e)
				if "hosts" in circuit.info:
					self.free_resources(circuit)
			raise e

	def complete_provider_submission(self, circuit, result=None):
		runtime = self.controller.complete_scheduled_task(
			circuit, result=result)
		if result is not None and runtime is not None:
			self.controller.publish_completion(result)
		return runtime

	def fail_provider_submission(self, circuit, error):
		return self.controller.fail_scheduled_task(
			circuit, error=error, reason="provider-submission-failed")

	def _failed_submission_status(self, cid, reservation_id):
		if cid is None:
			return None
		status = self.controller.task_status_for_cid(
			cid, reservation_id=reservation_id)
		if (status.get("outcome") == "FAILED" and
				status.get("reason")
				in MANAGED_SUBMISSION_FAILURE_REASONS):
			return status
		return None

	def finalize_provider_task(self, circuit, result=None):
		state = circuit.getState()
		if state == CircuitStates.FAIL:
			reason = (
				result.get("reason") if isinstance(result, dict) else None)
			reason = reason or "provider-execution-failed"
			return self.controller.fail_scheduled_task(
				circuit, reason=reason,
				publish_completion=result is None)
		if state in (CircuitStates.EXEC_DONE, CircuitStates.RESOURCES_CONSUMED):
			return self.controller.complete_scheduled_task(
				circuit, result=result)
		return None

	def cancel_provider_submission(self, cid, reason=None):
		runtime = self.controller.task_for_cid(cid)
		reservation_id = (
			runtime.reservation_id if runtime is not None else None)
		return self.cancel_task(
			cid=cid, reservation_id=reservation_id, reason=reason)

	def cancel_task(self, cid=None, reservation_id=None, token=None,
			reason=None, qtask_id=None):
		if reservation_id is not None:
			request = parse_execution_request(
				{}, reservation_id=reservation_id, token=token)
			self.require_managed_execution(request)
		status = self.controller.cancel_task(
			cid=cid, qtask_id=qtask_id, reason=reason,
			reservation_id=reservation_id,
			require_reservation=True)
		self.process_oor_queue()
		return status

	def task_status(self, cid=None, reservation_id=None, token=None,
		    qtask_id=None):
		if qtask_id is not None:
			return self.controller.task_status_for_qtask_id(
				qtask_id, reservation_id=reservation_id,
				require_reservation=True)
		return self.controller.task_status_for_cid(
			cid, reservation_id=reservation_id,
			require_reservation=True)

	def get_task_timing(self, token=None, reservation_id=None, task_id=None):
		status = self.controller.task_status_for_qtask_id(
			task_id, reservation_id=reservation_id, require_reservation=True)
		if status.get("outcome") == "INVALID_RESERVATION":
			return status
		provider_timing = self._provider_task_details(
			"get_task_timing", status.get("cid"))
		return {
			"task_id": task_id,
			"reservation_id": reservation_id,
			"lifecycle_state": status.get("lifecycle_state"),
			"timing": provider_timing,
		}

	def get_task_metadata(self, token=None, reservation_id=None, task_id=None):
		status = self.controller.task_status_for_qtask_id(
			task_id, reservation_id=reservation_id, require_reservation=True)
		if status.get("outcome") == "INVALID_RESERVATION":
			return status
		metadata = dict(status)
		metadata["task_id"] = task_id
		metadata["provider_metadata"] = self._provider_task_details(
			"get_task_metadata", status.get("cid"))
		return metadata

	def _provider_task_details(self, method_name, cid):
		method = getattr(self.qrc, method_name, None)
		if method is None:
			return {"available": False, "reason": "provider-unsupported"}
		result = method(cid)
		if isinstance(result, dict):
			return dict(result)
		return {"available": result is not None, "value": result}

	def get_telemetry_access_model(self, token=None):
		return self.controller.telemetry_access_model()

	def get_capacity_snapshot(self, token=None, device_id=None, scope_id=None,
				  access_class=None):
		return self.controller.capacity_snapshot(
			device_id=device_id, scope_id=scope_id,
			access_class=access_class or "manager-aggregate")

	def get_queue_metrics(self, token=None, device_id=None, access_class=None):
		return self.controller.queue_metrics(
			device_id=device_id,
			access_class=access_class or "manager-aggregate")

	def reconcile_runtime_state(self, token=None, reason=None):
		if not reason:
			raise DEFwExecutionError(
				"runtime reconciliation requires an audit reason")
		result = self.controller.reconcile_runtime_state()
		self.process_oor_queue()
		return self.controller.record_control_reconciliation(
			reason, result, token=token)

	def get_service_lifecycle_telemetry(self, token=None, access_class=None):
		return self.controller.service_lifecycle_telemetry(
			access_class=access_class or "operator")

	def record_defw_directory_event(self, event_type, service_record=None,
					peer_event=None, reason=None, details=None):
		return self.controller.record_defw_directory_event(
			event_type, service_record=service_record,
			peer_event=peer_event, reason=reason, details=details)

	def _cancel_provider_handle(self, status):
		provider_handle = status.get("provider_handle")
		if provider_handle is None or self.qrc is None:
			return
		cancel = getattr(self.qrc, "cancel", None)
		if cancel is not None:
			status["provider_cancel_status"] = cancel(provider_handle)
			return
		status["provider_cancel_status"] = "unsupported"

	def sync_run(self, info, reservation_id=None, token=None, timeout=None,
				 cancel_on_timeout=False):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		request = parse_execution_request(
			info,
			reservation_id=reservation_id,
			token=token,
			timeout=timeout,
			cancel_on_timeout=cancel_on_timeout,
		)
		self.require_managed_execution(request)
		return self._sync_run_request(request)

	def _sync_run_request(self, request):
		cid = self.create_circuit(request.payload, request=request)
		deadline = _sync_deadline(request.context.timeout)
		while True:
			circuit = None
			try:
				circuit = self._prepare_run_circuit(
					cid, require_selected_cid=True)
				result = self.submit_provider_sync(circuit)
				self.complete_provider_submission(circuit, result=result)
				response = self.controller.task_status_for_cid(
					circuit.get_cid(), outcome="COMPLETED",
					result=result,
					reservation_id=request.context.reservation_id)
				if "hosts" in circuit.info:
					self.free_resources(circuit)
				logging.debug(
					f"circuit {circuit.get_cid()} completed "
					f"with output {result}")
				return response
			except DEFwOutOfResources as error:
				if deadline is not None and _sync_timed_out(deadline):
					return self._sync_timeout_response(
						cid, request, str(error))
				_sleep_until_retry(deadline)
			except Exception as e:
				if circuit is not None:
					self.fail_provider_submission(circuit, e)
					if "hosts" in circuit.info:
						self.free_resources(circuit)
				status = self._failed_submission_status(
					cid, request.context.reservation_id)
				if status is not None:
					return status
				raise e

	def _sync_timeout_response(self, cid, request, message):
		runtime = self.controller.task_for_cid(cid)
		if runtime is None:
			return self.controller.task_status_for_cid(
				cid, outcome="TIMEOUT", reason="sync-timeout",
				message=message,
				reservation_id=request.context.reservation_id)
		if request.context.cancel_on_timeout:
			self.cancel_provider_submission(cid, reason="sync-timeout")
			return self.controller.task_status_for_cid(
				cid, outcome="CANCELLED", reason="sync-timeout",
				message=message,
				reservation_id=request.context.reservation_id)
		self.oor_queue.put(cid)
		self.controller.record_timeout(
			runtime.qtask_id, reason="sync-timeout", message=message)
		return self.controller.task_status_for_cid(
			cid, outcome="TIMEOUT", reason="sync-timeout",
			message=message,
			reservation_id=request.context.reservation_id)

	def require_managed_execution(self, request):
		service_state = getattr(self.controller, "service_state", "running")
		if service_state != "running":
			raise DEFwExecutionError(
				f"QPM is not accepting execution: "
				f"state={service_state}")
		if request.context.reservation_id is not None:
			try:
				self.controller.validate_reservation_for_context(
					request.context)
				credential_getter = getattr(
					self.controller,
					"provider_credential_for_reservation",
					None)
				if credential_getter is not None:
					credential_getter(
						request.context.reservation_id,
						operation="execution")
			except (QPMAdmissionUnavailable,
				QPMAdmissionValidationError) as error:
				raise DEFwExecutionError(str(error))
			return
		raise DEFwExecutionError(
			"reservation_id is required for resource-affecting QPM execution")

	def async_run_oor(self, cid):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		circuit = None
		try:
			circuit = self._prepare_run_circuit(
				cid, require_selected_cid=True)
			self.submit_provider_async(circuit)
		except DEFwOutOfResources as e:
			self.defer_local_retry(cid)
			self.oor_queue.put(cid)
			raise e
		except Exception as e:
			if circuit is not None:
				self.fail_provider_submission(circuit, e)
				if "hosts" in circuit.info:
					self.free_resources(circuit)
			self.process_oor_queue()
			raise e

	def async_run(self, info, reservation_id=None, token=None, timeout=None,
				  cancel_on_timeout=False):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		request = parse_execution_request(
			info,
			reservation_id=reservation_id,
			token=token,
			timeout=timeout,
			cancel_on_timeout=cancel_on_timeout,
		)
		self.require_managed_execution(request)
		return self._async_run_request(request)

	def _async_run_request(self, request):
		cid = None
		circuit = None
		try:
			cid = self.create_circuit(request.payload, request=request)
			circuit = self._prepare_run_circuit(
				cid, require_selected_cid=True)
			return self.submit_provider_async(circuit, return_status=True)
		except DEFwOutOfResources as e:
			if cid is None:
				raise e
			try:
				self.dispatch_ready_qtask()
			except DEFwOutOfResources:
				pass
			self.defer_local_retry(cid)
			self.oor_queue.put(cid)
		except Exception as e:
			if circuit is not None:
				self.fail_provider_submission(circuit, e)
				if "hosts" in circuit.info:
					self.free_resources(circuit)
			self.process_oor_queue()
			status = self._failed_submission_status(
				cid, request.context.reservation_id)
			if status is not None:
				return status
			raise e

		return self.controller.task_status_for_cid(
			cid, reservation_id=request.context.reservation_id)

	def defer_local_retry(self, cid):
		runtime = self.controller.task_for_cid(cid)
		if runtime is None:
			return
		if runtime.state in (QPM_TASK_QUEUED, QPM_TASK_SELECTED):
			return
		self.controller.set_task_state(
			runtime.qtask_id, QPM_TASK_PENDING_CAPACITY)

	def read_cq(self, cid=None, reservation_id=None, token=None):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		self.process_oor_queue()
		result = self.controller.read_completion(
			reservation_id=reservation_id, cid=cid,
			operation="read_cq")
		if result.get("completion_ready"):
			self.all_results.append(result)
		return result

	def peek_cq(self, cid=None, reservation_id=None, token=None):
		if not qpm_initialized:
			raise DEFwNotReady("QPM has not initialized properly")

		return self.controller.peek_completion(
			reservation_id=reservation_id, cid=cid,
			operation="peek_cq")

	def _result_selector_reservation_error(self, cid, reservation_id):
		if reservation_id is not None and cid is None:
			return {
				"outcome": "INVALID_RESERVATION",
				"lifecycle_state": "invalid-reservation",
				"reservation_id": reservation_id,
				"reason": "task-selector-required",
				"message": (
					"reservation-scoped result retrieval requires cid"),
			}
		return self.controller.task_reservation_error(
			cid=cid, reservation_id=reservation_id,
			require_reservation=True)

	def register_event_notification(self, ep, evtype, class_id,
					token=None, reservation_id=None, filters=None):
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
		register = getattr(self.qrc, "register_event_notification", None)
		if register is None:
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
		register(push_info)

	def is_ready(self, token=None):
		return self.get_service_status(token=token)

	def query_helper(self, type_bits, caps_bits, svc_name, svc_desc,
					 properties=None):
		from api_qpm_common import QPMType, QPMCapability
		from defw_agent_info import get_bit_list, get_bit_desc, Capability, DEFwServiceInfo
		properties = dict(properties or {})
		service_module = self.__class__.__module__
		service_class = self.__class__.__name__
		provider = properties.get("provider")
		properties.setdefault("service_type", QPM_SERVICE_TYPE)
		properties.setdefault("service_id", _qpm_service_id(
			svc_name, service_module, provider, properties))
		properties.setdefault("qpm_type", int(type_bits))
		properties.setdefault("qpm_capabilities", int(caps_bits))
		properties.setdefault(
			"simulator",
			_qpm_type_bit_enabled(type_bits, QPMType.QPM_TYPE_SIMULATOR))
		properties.setdefault(
			"hardware",
			_qpm_type_bit_enabled(type_bits, QPMType.QPM_TYPE_HARDWARE))
		properties.setdefault("selector", _qpm_selector(
			properties, svc_name, provider))
		properties.setdefault("api_bindings", _qpm_api_bindings(
			service_module, service_class))
		properties.setdefault("binding_name", "execution")
		properties.setdefault("client_module", "api_qpm_execution")
		properties.setdefault("client_class", "QPMExecution")
		properties.setdefault("service_module", service_module)
		properties.setdefault("service_class", service_class)
		properties.setdefault("controller", self.controller_telemetry())
		t = get_bit_list(type_bits, QPMType)
		c = get_bit_list(caps_bits, QPMCapability)
		cap = Capability(type_bits, caps_bits, get_bit_desc(t, c))
		info = DEFwServiceInfo(
			svc_name, svc_desc,
			service_class,
			service_module,
			cap, -1,
			properties=properties)
		return info

	def controller_telemetry(self):
		return self.controller.telemetry()

	def configure_device_profile(self, token=None, device_id=None,
				     profile=None, **overrides):
		if profile is None and isinstance(token, dict):
			profile = token
			token = None
		device_profile = dict(profile or {})
		if device_id is not None:
			device_profile["device_id"] = device_id
		device_profile.update(overrides)
		result = self.controller.configure_device_profile(device_profile)
		self.configure_default_admission_policy()
		return result

	def configure_default_admission_policy(self):
		current = self.controller.admission_configuration
		if current:
			return self.controller.set_admission_policy(current)
		profile = dict(self.controller.device_profile or {})
		return self.controller.set_admission_policy({
			"policy": {"name": "unlimited", "options": {}},
			"estimator": {"name": "baseline", "options": {}},
			"baseline": dict(profile.get("baseline") or {}),
			"capacity": {},
		})

	def get_device_profile(self, token=None, device_id=None):
		return self.controller.get_device_profile()

	def get_admission_policy(self, token=None, device_id=None):
		return self.controller.get_admission_policy(device_id=device_id)

	def set_admission_policy(self, token=None, device_id=None,
				 configuration=None):
		return self.controller.set_admission_policy(
			configuration, device_id=device_id)

	def configure_scheduler_policy(self, token=None, device_id=None,
				       configuration=None):
		return self.controller.configure_scheduler_policy(configuration)

	def get_scheduler_status(self, token=None, device_id=None):
		return self.controller.get_scheduler_status()

	def get_scheduler_policy(self, token=None, device_id=None):
		return self.controller.get_scheduler_policy()

	def pause_execution_target(self, token=None, device_id=None,
				   reason=None):
		return self.controller.pause_scheduler(reason=reason)

	def resume_execution_target(self, token=None, device_id=None):
		return self.controller.resume_scheduler()

	def drain_execution_target(self, token=None, device_id=None,
				   mode="graceful", timeout_s=None):
		return self.controller.drain_scheduler(
			mode=mode, timeout_s=timeout_s)

	def configure_dispatch_limits(self, token=None, device_id=None,
				      limits=None):
		return self.controller.configure_dispatch_limits(limits)

	def get_scheduler_queue_state(self, token=None, device_id=None,
				      include_restricted=False):
		return self.controller.get_scheduler_queue_state(
			include_restricted=include_restricted)

	def evaluate(self, token=None, request=None):
		return self.controller.evaluate_reservation(request, token=token)

	def reserve(self, token=None, request=None):
		if not isinstance(request, dict):
			raise DEFwExecutionError(
				"legacy service reservation is not supported by the "
				"QPM admission API")
		return self.controller.reserve_admission(request, token=token)

	def renew(self, token=None, reservation_id=None, request=None):
		return self.controller.renew_admission(
			reservation_id, request=request, token=token)

	def release(self, token=None, reservation_id=None, reason=None):
		if reservation_id is None or isinstance(
				reservation_id, (list, tuple, set)):
			raise DEFwExecutionError(
				"legacy service release is not supported by the "
				"QPM admission API")
		return self.controller.release_admission(
			reservation_id, reason_code=reason or 0, token=token)

	def cancel(self, token=None, reservation_id=None, reason=None):
		return self.controller.cancel_admission(
			reservation_id, reason_code=reason or 0, token=token)

	def get_reservation(self, token=None, reservation_id=None):
		return self.controller.get_admission_reservation(
			reservation_id, token=token)

	def list_reservations(self, token=None, filters=None):
		return self.controller.list_admission_reservations(
			filters=filters, token=token)

	def release_service(self, services=None):
		global qpm_shutdown

		qpm_shutdown = True
		self.controller.stop_completion_purge_worker()
		self.controller.clear_provider_credentials()
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

	def test(self, token=None):
		status = self.get_service_status(token=token)
		return {
			"status": "ok" if status["initialized"] else "not-ready",
			"rpc_ready": True,
			"service": status,
		}

	def get_service_status(self, token=None):
		return self.controller.get_service_status(
			initialized=qpm_initialized,
			provider_ready=self.qrc is not None)

	def shutdown(self, token=None, mode="graceful", timeout_s=None,
		     reason=None):
		if mode not in ("graceful", "cancel"):
			raise DEFwExecutionError(f"unsupported shutdown mode: {mode}")
		if timeout_s is not None and float(timeout_s) < 0:
			raise DEFwExecutionError("shutdown timeout must not be negative")
		request = self.controller.begin_service_shutdown(
			mode, timeout_s, reason, token=token)
		if request.get("repeated"):
			return request
		thread = threading.Thread(
			target=self._complete_service_shutdown,
			args=(mode, timeout_s),
			name=f"qpm-shutdown-{self.controller.config.target_id}",
			daemon=True)
		thread.start()
		return request

	def _complete_service_shutdown(self, mode, timeout_s):
		deadline = None
		if timeout_s is not None:
			deadline = time.monotonic() + float(timeout_s)
		if mode == "graceful":
			while self._active_runtime_task_ids():
				if deadline is not None and time.monotonic() >= deadline:
					mode = "cancel"
					break
				time.sleep(0.05)
		if mode == "cancel":
			self._cancel_active_runtime_tasks()
		self.controller.set_service_state("stopping")
		self.shutdown_provider()
		self.controller.set_service_state("stopped")
		time.sleep(0.05)
		me.exit()

	def _active_runtime_task_ids(self):
		with self.controller.lock:
			return [
				runtime.qtask_id
				for runtime in self.controller.runtime_by_qtask_id.values()
				if runtime.state not in QPM_TASK_TERMINAL_STATES
			]

	def _cancel_active_runtime_tasks(self):
		with self.controller.lock:
			tasks = [
				(runtime.qtask_id, runtime.reservation_id)
				for runtime in self.controller.runtime_by_qtask_id.values()
				if runtime.state not in QPM_TASK_TERMINAL_STATES
			]
		for task_id, reservation_id in tasks:
			self.controller.cancel_task(
				qtask_id=task_id, reservation_id=reservation_id,
				require_reservation=False, reason="service-shutdown")

	def shutdown_provider(self):
		self.controller.stop_completion_purge_worker()
		self.controller.clear_provider_credentials()
		if self.qrc:
			self.qrc.shutdown()
			self.qrc = None


def _sync_deadline(timeout):
	if timeout is None:
		return None
	timeout_s = float(timeout)
	if timeout_s <= 0:
		return time.time()
	return time.time() + timeout_s


def _sync_timed_out(deadline):
	return time.time() >= deadline


def _sleep_until_retry(deadline):
	if deadline is None:
		time.sleep(0.01)
		return
	remaining = max(0.0, deadline - time.time())
	time.sleep(min(0.01, remaining))
