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
		self.queue = []
		self.states = {}
		self.started = []
		self.completed = []
		self.failed = []
		self.cancelled = []

	def set_policy(self, name, options):
		self.policy = (name, dict(options))

	def submit_task(self, task):
		task = dict(task)
		self.submitted.append(task)
		self.queue.append(task["task_id"])
		self.states[task["task_id"]] = "queued"
		return task["task_id"]

	def select_next_assignment(self):
		if not self.queue:
			return None
		task_id = self.queue.pop(0)
		self.states[task_id] = "selected"
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


class FakeServiceInfo:
	def __init__(self, qpm, endpoint="fake-qpm-endpoint", properties=None,
				 module_name="svc_fake_qpm"):
		self.qpm = qpm
		self.endpoint = endpoint
		self.properties = dict(properties or {})
		self.module_name = module_name

	def get_properties(self):
		return dict(self.properties)

	def get_endpoint(self):
		return self.endpoint

	def get_module_name(self):
		return self.module_name


class FakeResourceManager:
	def __init__(self, service_infos=None):
		self.service_infos = list(service_infos or [])
		self.requests = []

	def get_services(self, service_name, qpm_type=-1, qpm_cap=-1):
		self.requests.append((service_name, qpm_type, qpm_cap))
		return list(self.service_infos)


class FakeDefwModule:
	def __init__(self):
		self.connections = []

	def connect_to_resource(self, service_infos, resource_name):
		self.connections.append((list(service_infos), resource_name))
		return [service_info.qpm for service_info in service_infos]


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
