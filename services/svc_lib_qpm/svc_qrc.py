# Run-queue for the shim service (svc_lib_qpm). Same sync/async execution and
# results bookkeeping as the native IQM run-queue, but every driver call goes
# through the bifurcation Frontend, which routes it to QRMI or QDMI.

from api_events import Event
from defw_exception import DEFwExecutionError
from .descriptor import resolve_descriptor
from .frontend import Frontend
from .drivers.qrmi_driver import QrmiDriver
from .drivers.qdmi_driver import QdmiDriver
import logging
import threading
import time

_DRIVER_FACTORY = {"qrmi": QrmiDriver, "qdmi": QdmiDriver}


def _cancel_requested(circ):
	cancel_event = getattr(circ, "cancel_event", None)
	return cancel_event is not None and cancel_event.is_set()


class QRC:
	def __init__(self, start=True):
		self.shutdown_workers = False
		self.circuit_results = []
		self.circuit_results_lock = threading.Lock()
		self.push_info = {}
		self.threads = []
		# Cancel events for the circuits async_run has in flight, by cid. The
		# QPM controller cancels a running circuit through cancel() below.
		self._cancel_events = {}
		self._cancel_lock = threading.Lock()
		# Per-resource descriptor drives the bifurcation: only the libraries
		# wired for this resource get a driver, and the Frontend routes each
		# call per the descriptor's caps (QFW_QPU_IFACE_PREF breaks ties).
		descriptor = resolve_descriptor()
		drivers = [_DRIVER_FACTORY[name](descriptor)
				for name in descriptor.get("libraries", [])
				if name in _DRIVER_FACTORY]
		self.frontend = Frontend(drivers, descriptor)

	def _result_dict(self, circ, output, rc):
		return {
			'cid': circ.get_cid(),
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

	def _run_circuit(self, circ, raise_on_error):
		try:
			circ.set_launching()
			circ.set_running()
			output = self.frontend.run_circuit(
				circ, lib=circ.info.get("lib"))
			circ.set_exec_done()
			return self._result_dict(circ, output, 0)
		except Exception as e:
			circ.set_fail()
			if raise_on_error:
				raise DEFwExecutionError(str(e)) from e
			cancelled = _cancel_requested(circ)
			if cancelled:
				logging.info(f"shim circuit {circ.get_cid()} cancelled: {e}")
			else:
				logging.critical(f"shim circuit {circ.get_cid()} failed: {e}")
			output = {
				'counts': {},
				'shim': {
					'error': str(e),
					'error_type': type(e).__name__,
				},
			}
			result = self._result_dict(circ, output, -1)
			if cancelled:
				# The QPM already reported this task cancelled. Mark the result
				# the runner still delivers the same way, as the fake IQM QRC
				# does, so it does not read as an execution failure.
				result['outcome'] = 'FAILED'
				result['reason'] = 'provider-cancelled'
			return result

	def _async_runner(self, circ):
		result = None
		try:
			result = self._run_circuit(circ, raise_on_error=False)
		finally:
			with self._cancel_lock:
				self._cancel_events.pop(circ.get_cid(), None)
			circ.free_resources(circ, result=result)
		if result is None:
			return
		self._push_or_store_result(result)

	def sync_run(self, circ):
		return self._run_circuit(circ, raise_on_error=True)

	def async_run(self, circ):
		cid = circ.get_cid()
		# The driver watches this while it waits on the provider, and stops the
		# provider job once it is set. See cancel().
		cancel_event = threading.Event()
		circ.cancel_event = cancel_event
		with self._cancel_lock:
			self._cancel_events[cid] = cancel_event
		runner = threading.Thread(target=self._async_runner, args=(circ,))
		runner.daemon = True
		runner.start()
		self.threads.append(runner)
		return cid

	def cancel(self, provider_handle):
		# The QPM controller's provider canceller: UTIL_QPM wires in this
		# method, and provider_handle is the cid async_run returned. The
		# controller calls it while holding its lock, so it only signals the
		# circuit's runner. The driver stops the provider job at its next poll
		# (QRMI task_stop, FoMaC job.cancel()). "cancelled" and "not-found"
		# follow the fake IQM QRC, and the controller treats both as final.
		with self._cancel_lock:
			cancel_event = self._cancel_events.get(provider_handle)
		if cancel_event is None:
			return "not-found"
		cancel_event.set()
		return "cancelled"

	def read_cq(self, cid=None):
		with self.circuit_results_lock:
			for index, result in enumerate(self.circuit_results):
				if cid is None or result['cid'] == cid:
					result = self.circuit_results.pop(index)
					result['cq_dequeue_time'] = time.time()
					return result
		return None

	def peak_cq(self, cid=None):
		with self.circuit_results_lock:
			for result in self.circuit_results:
				if cid is None or result['cid'] == cid:
					return result
		return None

	def register_event_notification(self, info):
		self.push_info = info

	def capability_map(self):
		return self.frontend.capability_map()

	def get_backend_info(self, lib=None):
		return self.frontend.get_backend_info(lib=lib)

	def get_device_info(self, lib=None):
		return self.frontend.get_device_info(lib=lib)

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None):
		return self.frontend.get_dynamic_backend_info(
			calibration_set_id, lib=lib)

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None):
		return self.frontend.get_calibration_snapshot(
			calibration_set_id, lib=lib)

	def get_coupling_graph(self, calibration_set_id=None, lib=None):
		return self.frontend.get_coupling_graph(calibration_set_id, lib=lib)

	def get_task_timing(self, cid=None, lib=None):
		return self.frontend.get_task_timing(cid, lib=lib)

	def get_task_metadata(self, cid=None, lib=None):
		return self.frontend.get_task_metadata(cid, lib=lib)

	def shutdown(self):
		self.shutdown_workers = True
		# Nothing can deliver these results once the QPM is gone, so stop the
		# provider jobs rather than leave them running.
		with self._cancel_lock:
			cancel_events = list(self._cancel_events.values())
		for cancel_event in cancel_events:
			cancel_event.set()
		for thread in self.threads:
			if thread.is_alive():
				thread.join(timeout=1)
