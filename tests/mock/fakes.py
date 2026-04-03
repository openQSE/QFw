class FakeQPM:
	def __init__(self, cids=None, async_error=None, test_error=None):
		self.cids = list(cids or ["cid-1"])
		self.async_error = async_error
		self.test_error = test_error
		self.registrations = []
		self.submitted_payloads = []
		self.shutdown_called = False

	def register_event_notification(self, endpoint, event_type, class_id):
		self.registrations.append(
			{
				"endpoint": endpoint,
				"event_type": event_type,
				"class_id": class_id,
			}
		)

	def async_run(self, info):
		self.submitted_payloads.append(info)
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
