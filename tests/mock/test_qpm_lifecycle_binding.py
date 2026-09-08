import os
import threading
import time

import pytest

from api_qpm_common import (
	QPMBindingUnavailable,
	QPMLifecycleBinding,
	QPMRuntimeChanged,
)


def service_record(runtime_id="qpm-runtime-1", peer_handle="qpm-peer-1",
		generation=1):
	return {
		"service_id": "nwqsim",
		"service_name": "NWQSim QPM",
		"service_type": "qfw.qpm",
		"runtime_id": runtime_id,
		"peer_handle": peer_handle,
		"generation": generation,
		"endpoint": {
			"address": "nwqsim-head",
			"listen_port": 8490,
		},
		"api_bindings": [
			{
				"binding_name": name,
				"client_module": f"api_qpm_{name}",
				"client_class": f"QPM{name.title()}",
				"service_module": "svc_nwqsim_qpm.svc_qpm",
				"service_class": "QPM",
			}
			for name in ("execution", "admission", "control")
		],
	}


class FakeEndpoint:
	def get_id(self):
		return "client-runtime-1"


class FakeRuntime:
	def my_endpoint(self):
		return FakeEndpoint()


class FakeClient:
	def __init__(self, binding):
		self.binding = binding

	def identify(self):
		return (
			self.binding["service_record"]["runtime_id"],
			self.binding["selected_binding"]["binding_name"],
		)


class FakeDEFw:
	def __init__(self):
		self.me = FakeRuntime()
		self.connections = []
		self.dirsvc = None

	def connect_to_binding(self, binding):
		self.connections.append(binding)
		return FakeClient(binding)


class BlockingDEFw(FakeDEFw):
	def __init__(self):
		super().__init__()
		self.connect_entered = threading.Event()
		self.connect_release = threading.Event()

	def connect_to_binding(self, binding):
		self.connect_entered.set()
		self.connect_release.wait(timeout=2)
		return super().connect_to_binding(binding)


class FakeEventAPI:
	def __init__(self):
		self.read_fd, self.write_fd = os.pipe()
		self.events = []
		self.lock = threading.Lock()
		self.registered = False

	def class_id(self):
		return "qpm-lifecycle-callback"

	def register_external(self):
		self.registered = True

	def unregister_external(self):
		self.registered = False

	def fileno(self):
		return self.read_fd

	def put(self, event):
		with self.lock:
			self.events.append(event)
			os.write(self.write_fd, b"x")

	def get(self):
		with self.lock:
			events = list(self.events)
			self.events.clear()
			os.read(self.read_fd, len(events))
			return events


class FakePeerEvents:
	def __init__(self):
		self.listeners = []

	def add_peer_event_listener(self, listener):
		self.listeners.append(listener)
		return listener

	def remove_peer_event_listener(self, listener):
		if listener in self.listeners:
			self.listeners.remove(listener)

	def is_dirsvc_peer_event(self, event):
		return bool(event.get("directory"))

	def emit(self, event):
		for listener in list(self.listeners):
			listener(dict(event))


class FakeDirectory:
	def __init__(self, record):
		self.record = record
		self.calls = []
		self.registrations = {}

	def register_event_notification(self, endpoint, event_type, class_id,
			filters=None):
		registration_id = f"registration-{len(self.registrations) + 1}"
		self.calls.append(("register", event_type))
		self.registrations[registration_id] = {
			"endpoint": endpoint,
			"event_type": event_type,
			"class_id": class_id,
			"filters": filters,
		}
		return registration_id

	def unregister_event_notification(self, registration_id):
		self.calls.append(("unregister", registration_id))
		return self.registrations.pop(registration_id, None) is not None

	def resolve_services(self, **filters):
		self.calls.append(("resolve", dict(filters)))
		if filters.get("service_id") != self.record["service_id"]:
			return []
		return [
			{
				"service_record": self.record,
				"selected_binding": binding,
			}
			for binding in self.record["api_bindings"]
		]


def wait_for(predicate, message):
	deadline = time.monotonic() + 2
	while time.monotonic() < deadline:
		if predicate():
			return
		time.sleep(0.01)
	raise AssertionError(message)


def connected_event(record, directory_runtime_id="directory-runtime-1"):
	return {
		"event": "SERVICE_CONNECTED",
		"directory_runtime_id": directory_runtime_id,
		"service_id": record["service_id"],
		"runtime_id": record["runtime_id"],
		"peer_handle": record["peer_handle"],
		"service_record": record,
	}


def disconnected_event(record, directory_runtime_id="directory-runtime-1"):
	return {
		"event": "SERVICE_DISCONNECTED",
		"directory_runtime_id": directory_runtime_id,
		"service_id": record["service_id"],
		"runtime_id": record["runtime_id"],
		"peer_handle": record["peer_handle"],
		"reason": "socket-close",
	}


def test_managed_binding_tracks_qpm_and_directory_lifecycle():
	defw = FakeDEFw()
	peer_events = FakePeerEvents()
	event_api = FakeEventAPI()
	first_record = service_record()
	first_directory = FakeDirectory(first_record)
	directory_holder = {"client": first_directory}
	defw.dirsvc = first_directory
	reconnects = []
	binding = QPMLifecycleBinding(
		"nwqsim",
		directory_getter=lambda: directory_holder["client"],
		defw_module=defw,
		peer_events_module=peer_events,
		event_api_factory=lambda: event_api,
		recovery_timeout=1,
	)
	binding.add_reconnect_listener(reconnects.append)
	binding.start(
		directory=first_directory,
		directory_runtime_id="directory-runtime-1",
	)

	assert [item[0] for item in first_directory.calls[:3]] == [
		"register", "register", "resolve",
	]
	api = binding.api("execution", expected_runtime_id="qpm-runtime-1")
	assert api.identify() == ("qpm-runtime-1", "execution")
	assert binding.snapshot()["available"]

	event_api.put(disconnected_event(first_record))
	wait_for(
		lambda: not binding.snapshot()["available"],
		"active QPM disconnect did not invalidate the binding",
	)
	with pytest.raises(QPMBindingUnavailable):
		api.identify()

	same_record = service_record(peer_handle="qpm-peer-2", generation=2)
	event_api.put(connected_event(same_record))
	wait_for(
		lambda: binding.snapshot()["peer_handle"] == "qpm-peer-2",
		"same-runtime reconnect did not restore the binding",
	)
	assert api.identify() == ("qpm-runtime-1", "execution")
	assert reconnects[-1]["same_runtime"]

	event_api.put(disconnected_event(first_record))
	time.sleep(0.05)
	assert binding.snapshot()["available"]

	replacement = service_record(
		runtime_id="qpm-runtime-2",
		peer_handle="qpm-peer-3",
		generation=3,
	)
	event_api.put(connected_event(replacement))
	wait_for(
		lambda: binding.snapshot()["runtime_id"] == "qpm-runtime-2",
		"replacement QPM runtime was not installed",
	)
	with pytest.raises(QPMRuntimeChanged):
		api.identify()
	assert binding.api("execution").identify() == (
		"qpm-runtime-2", "execution")
	assert not reconnects[-1]["same_runtime"]

	second_directory = FakeDirectory(replacement)
	directory_holder["client"] = second_directory
	defw.dirsvc = second_directory
	peer_events.emit({
		"directory": True,
		"event_type": "PEER_LOST",
		"remote_runtime_id": "directory-runtime-1",
	})
	assert not binding.snapshot()["available"]
	peer_events.emit({
		"directory": True,
		"event_type": "PEER_READY",
		"remote_runtime_id": "directory-runtime-2",
	})
	wait_for(
		lambda: binding.snapshot()["directory_runtime_id"] ==
			"directory-runtime-2" and binding.snapshot()["available"],
		"replacement directory did not restore the QPM binding",
	)
	event_api.put(connected_event(
		first_record,
		directory_runtime_id="directory-runtime-1",
	))
	time.sleep(0.05)
	assert binding.snapshot()["runtime_id"] == "qpm-runtime-2"
	assert binding.api("execution").identify() == (
		"qpm-runtime-2", "execution")

	binding.close()
	assert not event_api.registered
	assert peer_events.listeners == []


def test_qpm_disconnect_wins_race_with_direct_binding_creation():
	defw = BlockingDEFw()
	peer_events = FakePeerEvents()
	event_api = FakeEventAPI()
	record = service_record()
	directory = FakeDirectory(record)
	binding = QPMLifecycleBinding(
		"nwqsim",
		defw_module=defw,
		peer_events_module=peer_events,
		event_api_factory=lambda: event_api,
	)
	binding.start(
		directory=directory,
		directory_runtime_id="directory-runtime-1",
	)
	result = {}

	def connect():
		try:
			binding.client("execution")
		except Exception as error:
			result["error"] = error

	thread = threading.Thread(target=connect)
	thread.start()
	assert defw.connect_entered.wait(timeout=1)
	event_api.put(disconnected_event(record))
	wait_for(
		lambda: not binding.snapshot()["available"],
		"disconnect did not invalidate the racing binding",
	)
	defw.connect_release.set()
	thread.join(timeout=1)
	assert isinstance(result.get("error"), QPMBindingUnavailable)
	binding.close()
