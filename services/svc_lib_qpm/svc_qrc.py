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


class QRC:
	def __init__(self, start=True):
		self.shutdown_workers = False
		self.circuit_results = []
		self.circuit_results_lock = threading.Lock()
		self.push_info = {}
		self.threads = []
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
				self.push_info['class'].put(event)
			except Exception as e:
				logging.critical(
					"Failed to push event to client. "
					f"Exception encountered {e}")
				raise e
			return

		with self.circuit_results_lock:
			self.circuit_results.append(result)

	def _run_circuit(self, circ, raise_on_error):
		try:
			circ.set_launching()
			circ.set_running()
			output = self.frontend.run_circuit(circ)
			circ.set_exec_done()
			return self._result_dict(circ, output, 0)
		except Exception as e:
			circ.set_fail()
			if raise_on_error:
				raise DEFwExecutionError(str(e)) from e
			logging.critical(f"shim circuit {circ.get_cid()} failed: {e}")
			output = {
				'counts': {},
				'shim': {
					'error': str(e),
					'error_type': type(e).__name__,
				},
			}
			return self._result_dict(circ, output, -1)

	def _async_runner(self, circ):
		try:
			result = self._run_circuit(circ, raise_on_error=False)
		finally:
			circ.free_resources(circ)
		self._push_or_store_result(result)

	def sync_run(self, circ):
		return self._run_circuit(circ, raise_on_error=True)

	def async_run(self, circ):
		cid = circ.get_cid()
		runner = threading.Thread(target=self._async_runner, args=(circ,))
		runner.daemon = True
		runner.start()
		self.threads.append(runner)
		return cid

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

	def get_backend_info(self):
		return self.frontend.get_backend_info()

	def get_device_info(self):
		return self.frontend.get_device_info()

	def get_dynamic_backend_info(self, calibration_set_id=None):
		return self.frontend.get_dynamic_backend_info(calibration_set_id)

	def get_calibration_snapshot(self, calibration_set_id=None):
		return self.frontend.get_calibration_snapshot(calibration_set_id)

	def get_coupling_graph(self, calibration_set_id=None):
		return self.frontend.get_coupling_graph(calibration_set_id)

	def get_last_job_timing(self, cid=None):
		return self.frontend.get_last_job_timing(cid)

	def get_last_job_metadata(self, cid=None):
		return self.frontend.get_last_job_metadata(cid)

	def shutdown(self):
		self.shutdown_workers = True
		for thread in self.threads:
			if thread.is_alive():
				thread.join(timeout=1)
