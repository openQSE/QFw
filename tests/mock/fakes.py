class FakeQPM:
	def __init__(self, cids=None, async_error=None, test_error=None):
		self.cids = list(cids or ["cid-1"])
		self.async_error = async_error
		self.test_error = test_error
		self.registrations = []
		self.submitted_payloads = []
		self.shutdown_called = False

	def register_event_notification(self, endpoint, event_type, class_id,
					**kwargs):
		registration = {
			"endpoint": endpoint,
			"event_type": event_type,
			"class_id": class_id,
		}
		registration.update(kwargs)
		self.registrations.append(registration)

	def async_run(self, info, **kwargs):
		payload = dict(info)
		payload.update(kwargs)
		self.submitted_payloads.append(payload)
		if self.async_error is not None:
			raise self.async_error
		if self.cids:
			return self.cids.pop(0)
		return f"cid-{len(self.submitted_payloads)}"

	def shutdown(self):
		self.shutdown_called = True

	def test(self):
		if self.test_error is not None:
			raise self.test_error
		return "ok"


class FakeSchedulerContext:
	available = True

	def __init__(self, threading_mode, target_id=None):
		self.threading = threading_mode
		self.target_id = target_id
		self.policy = None
		self.submitted = []
		self.tasks = {}
		self.task_sequence = {}
		self.queue = []
		self.states = {}
		self.started = []
		self.completed = []
		self.failed = []
		self.cancelled = []
		self.round_robin_index = 0

	def set_policy(self, name, options):
		self.policy = (name, dict(options))

	def submit_task(self, task):
		task = dict(task)
		task_id = task["task_id"]
		self.submitted.append(task)
		self.tasks[task_id] = task
		self.task_sequence[task_id] = len(self.task_sequence)
		self.queue.append(task_id)
		self.states[task_id] = "queued"
		return task_id

	def select_next_assignment(self):
		if not self.queue:
			return None
		index = self._selected_queue_index()
		task_id = self.queue.pop(index)
		self.states[task_id] = "selected"
		return {"task_id": task_id}

	def _selected_queue_index(self):
		name = (self.policy or ("fifo", {}))[0]
		if name == "priority":
			return self._priority_queue_index()
		if name == "round_robin":
			return self._round_robin_queue_index()
		if name == "ordered":
			order = self._ordered_key()
			if order == 2:
				return self._runtime_queue_index(reverse=False)
			if order == 3:
				return self._runtime_queue_index(reverse=True)
		return 0

	def _priority_queue_index(self):
		return min(
			range(len(self.queue)),
			key=lambda index: (
				-self._task(self.queue[index]).get("priority", 0),
				self.task_sequence.get(self.queue[index], index)))

	def _runtime_queue_index(self, reverse=False):
		def key(index):
			task_id = self.queue[index]
			runtime = self._task(task_id).get("estimated_runtime_ns", 0)
			if reverse:
				runtime = -runtime
			return runtime, self.task_sequence.get(task_id, index)
		return min(range(len(self.queue)), key=key)

	def _round_robin_queue_index(self):
		groups = []
		group_to_indices = {}
		for index, task_id in enumerate(self.queue):
			task = self._task(task_id)
			group = (
				task.get("reservation_id") or
				task.get("job_id") or
				("task", task_id))
			if group not in group_to_indices:
				group_to_indices[group] = []
				groups.append(group)
			group_to_indices[group].append(index)
		group = groups[self.round_robin_index % len(groups)]
		self.round_robin_index += 1
		return group_to_indices[group][0]

	def _ordered_key(self):
		options = (self.policy or ("ordered", {}))[1]
		for value in options.values():
			return int(value)
		return 1

	def _task(self, task_id):
		if task_id in self.tasks:
			return self.tasks[task_id]
		return {"task_id": task_id}

	def task_started(self, task_id):
		self.states[task_id] = "running"
		self.started.append(task_id)

	def task_completed(self, task_id):
		self.states[task_id] = "completed"
		self.completed.append(task_id)

	def task_failed(self, task_id):
		self.states[task_id] = "failed"
		self.failed.append(task_id)

	def task_cancelled(self, task_id):
		self.states[task_id] = "cancelled"
		self.cancelled.append(task_id)

	def task_state(self, task_id):
		return self.states.get(task_id, "unknown")

	def task_count(self):
		return len(self.queue)


class FakeEvent:
	def __init__(self, payload):
		self.payload = payload

	def get_event(self):
		return self.payload


class FakeEventAPI:
	def __init__(self, events=None, class_id="event-api-1", fd=10):
		self.events = list(events or [])
		self._class_id = class_id
		self._fd = fd
		self.registered = False

	def register_external(self):
		self.registered = True

	def class_id(self):
		return self._class_id

	def fileno(self):
		return self._fd

	def get(self):
		events = list(self.events)
		self.events.clear()
		return events


class FakeRuntime:
	def __init__(self, endpoint="fake-endpoint"):
		self.endpoint = endpoint
		self.exit_called = False

	def my_endpoint(self):
		return self.endpoint

	def exit(self):
		self.exit_called = True


class FakeCircuit:
	def __init__(self, num_qubits, name="circuit", num_clbits=None, cregs=None):
		self.num_qubits = num_qubits
		self.name = name
		self.num_clbits = num_qubits if num_clbits is None else num_clbits
		self.cregs = list(cregs or [])
		self.metadata = {}


class FakeClassicalRegister:
	def __init__(self, name, size):
		self.name = name
		self.size = size


def make_result_event(cid, counts, *, offset=0.0):
	return FakeEvent(
		{
			"cid": cid,
			"creation_time": 1.0 + offset,
			"launch_time": 2.0 + offset,
			"resources_consumed_time": 3.0 + offset,
			"exec_time": 4.0 + offset,
			"completion_time": 5.0 + offset,
			"cq_enqueue_time": 1.5 + offset,
			"cq_dequeue_time": 2.5 + offset,
			"result": counts,
		}
	)
