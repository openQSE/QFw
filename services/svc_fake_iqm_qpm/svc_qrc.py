import os
import threading
import time


class QRC:
	def __init__(self, start=True, target_id="fake-iqm-20q"):
		self.target_id = target_id
		self.start = start
		self.push_info = {}
		self._lock = threading.Lock()
		self._tasks = {}
		self._results = []
		self._last_timing = {}
		self._last_metadata = {}

	def sync_run(self, circuit):
		return self._execute(circuit)

	def async_run(self, circuit):
		handle = f"fake-iqm-{circuit.info['qtask_id']}"
		cancel_event = threading.Event()
		thread = threading.Thread(
			target=self._execute_async,
			args=(handle, cancel_event, circuit),
			name=f"fake-iqm-qtask-{circuit.info['qtask_id']}")
		thread.daemon = True
		with self._lock:
			self._tasks[handle] = {
				"cancel": cancel_event,
				"thread": thread,
				"cid": circuit.get_cid(),
			}
		thread.start()
		return handle

	def cancel(self, provider_handle):
		with self._lock:
			task = self._tasks.get(provider_handle)
		if task is None:
			return "not-found"
		task["cancel"].set()
		return "cancelled"

	def register_event_notification(self, info):
		self.push_info = dict(info or {})

	def read_cq(self, cid=None):
		with self._lock:
			for index, result in enumerate(self._results):
				if cid is None or result.get("cid") == cid:
					return self._results.pop(index)
		return None

	def peak_cq(self, cid=None):
		with self._lock:
			for result in self._results:
				if cid is None or result.get("cid") == cid:
					return dict(result)
		return None

	def get_last_job_timing(self, cid=None):
		with self._lock:
			if cid is None and self._last_timing:
				return dict(next(reversed(self._last_timing.values())))
			return dict(self._last_timing.get(cid, {}))

	def get_last_job_metadata(self, cid=None):
		with self._lock:
			if cid is None and self._last_metadata:
				return dict(next(reversed(self._last_metadata.values())))
			return dict(self._last_metadata.get(cid, {}))

	def shutdown(self):
		with self._lock:
			tasks = list(self._tasks.values())
		for task in tasks:
			task["cancel"].set()

	def _execute_async(self, handle, cancel_event, circuit):
		try:
			result = self._execute(circuit, cancel_event=cancel_event)
			self._store_result(result)
			circuit.free_resources(circuit, result=result)
			self._push_result(result)
		finally:
			with self._lock:
				self._tasks.pop(handle, None)

	def _execute(self, circuit, cancel_event=None):
		info = circuit.info
		start_ns = time.time_ns()
		circuit.set_running()
		sleep_seconds = self._sleep_seconds(info)
		cancelled = self._sleep_or_cancel(sleep_seconds, cancel_event)
		end_ns = time.time_ns()
		observed_ns = end_ns - start_ns
		if cancelled:
			circuit.set_fail()
		else:
			circuit.set_exec_done()
		result = self._result(circuit, observed_ns, cancelled)
		self._remember_result_metadata(result)
		return result

	def _sleep_or_cancel(self, seconds, cancel_event):
		deadline = time.monotonic() + seconds
		while True:
			if cancel_event is not None and cancel_event.is_set():
				return True
			remaining = deadline - time.monotonic()
			if remaining <= 0:
				return False
			time.sleep(min(remaining, 0.01))

	def _sleep_seconds(self, info):
		estimate = dict(info.get("admission_estimate") or {})
		estimated_ns = (
			estimate.get("total_ns") or
			info.get("estimated_ns") or
			info.get("estimated_device_ns") or
			0)
		scale = _float_env("QFW_FAKE_QPM_SLEEP_SCALE", 1.0)
		max_sleep = _float_env("QFW_FAKE_QPM_MAX_SLEEP_SECONDS", 0.2)
		min_sleep = _float_env("QFW_FAKE_QPM_MIN_SLEEP_SECONDS", 0.001)
		scaled = float(estimated_ns) * scale / 1_000_000_000.0
		return min(max(scaled, min_sleep), max_sleep)

	def _result(self, circuit, observed_ns, cancelled):
		info = circuit.info
		cid = circuit.get_cid()
		qtask_id = info.get("qtask_id")
		shots = int(info.get("num_shots", info.get("shots", 1024)))
		num_qubits = int(info.get("num_qubits", 1))
		state = "0" * max(1, num_qubits)
		estimate = dict(info.get("admission_estimate") or {})
		result = {
			"cid": cid,
			"qtask_id": qtask_id,
			"reservation_id": info.get("reservation_id"),
			"outcome": "FAILED" if cancelled else "COMPLETED",
			"rc": 1 if cancelled else 0,
			"result": {} if cancelled else {state: shots},
			"provider": "fake-iqm",
			"target_id": self.target_id,
			"estimated_device_ns": (
				estimate.get("total_ns") or info.get("estimated_device_ns")),
			"baseline_units": (
				estimate.get("baseline_units") or info.get("baseline_units")),
			"observed_fake_runtime_ns": observed_ns,
			"admission_estimate": estimate,
			"requested_timing_metadata": {
				"num_qubits": num_qubits,
				"shots": shots,
				"depth": info.get("depth"),
				"one_q_gate_count": info.get("one_q_gate_count"),
				"two_q_gate_count": info.get("two_q_gate_count"),
				"measurement_count": info.get("measurement_count"),
			},
		}
		if cancelled:
			result["reason"] = "provider-cancelled"
		return result

	def _remember_result_metadata(self, result):
		cid = result.get("cid")
		timing = {
			"cid": cid,
			"qtask_id": result.get("qtask_id"),
			"reservation_id": result.get("reservation_id"),
			"estimated_device_ns": result.get("estimated_device_ns"),
			"observed_fake_runtime_ns": result.get(
				"observed_fake_runtime_ns"),
			"baseline_units": result.get("baseline_units"),
		}
		metadata = {
			"cid": cid,
			"qtask_id": result.get("qtask_id"),
			"provider": result.get("provider"),
			"target_id": result.get("target_id"),
			"admission_estimate": dict(result.get("admission_estimate") or {}),
			"requested_timing_metadata": dict(
				result.get("requested_timing_metadata") or {}),
		}
		with self._lock:
			self._last_timing[cid] = timing
			self._last_metadata[cid] = metadata

	def _store_result(self, result):
		with self._lock:
			self._results.append(dict(result))

	def _push_result(self, result):
		if not self.push_info:
			return
		event = _CompletionEvent(self.push_info["evtype"], result)
		delivered = self.push_info["class"].put(event)
		if delivered is False:
			return
		with self._lock:
			self._results = [
				item for item in self._results
				if item.get("cid") != result.get("cid")
			]


def _float_env(name, default):
	try:
		return float(os.environ.get(name, default))
	except (TypeError, ValueError):
		return float(default)


class _CompletionEvent:
	def __init__(self, evtype, payload):
		self.evtype = evtype
		self.payload = payload

	def get_evtype(self):
		return self.evtype

	def get_event(self):
		return self.payload
