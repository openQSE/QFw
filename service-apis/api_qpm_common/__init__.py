from defw_remote import BaseRemote
from enum import IntFlag
import copy
import select
import threading
import time


VERSION = 0.1
QPM_SERVICE_TYPE = "qfw.qpm"
SERVICE_CONNECTED = "SERVICE_CONNECTED"
SERVICE_DISCONNECTED = "SERVICE_DISCONNECTED"


class QPMType(IntFlag):
	QPM_TYPE_HARDWARE = 1 << 0
	QPM_TYPE_SIMULATOR = 1 << 1


class QPMCapability(IntFlag):
	QPM_CAP_TENSORNETWORK = 1 << 0
	QPM_CAP_STATEVECTOR = 1 << 1
	QPM_CAP_SUPERCONDUCTING = 1 << 2


class QPMRemoteBase(BaseRemote):
	pass


class QPMBindingError(RuntimeError):
	pass


class QPMBindingUnavailable(QPMBindingError):
	pass


class QPMRuntimeChanged(QPMBindingError):
	pass


class QPMManagedAPI:
	def __init__(self, binding, binding_name, expected_runtime_id=None):
		self.lifecycle_binding = binding
		self.binding_name = binding_name
		self.expected_runtime_id = expected_runtime_id

	def __getattr__(self, name):
		def invoke(*args, **kwargs):
			client = self.lifecycle_binding.client(
				self.binding_name,
				expected_runtime_id=self.expected_runtime_id,
			)
			return getattr(client, name)(*args, **kwargs)
		return invoke


class QPMLifecycleBinding:
	def __init__(self, service_id, directory_getter=None, defw_module=None,
		     peer_events_module=None, event_api_factory=None,
		     recovery_timeout=10):
		if not service_id:
			raise QPMBindingError("QPM lifecycle binding requires service_id")
		if defw_module is None:
			import defw as defw_module
		if peer_events_module is None:
			import defw_workers as peer_events_module
		if event_api_factory is None:
			from defw_event_baseapi import BaseEventAPI
			event_api_factory = BaseEventAPI
		self.service_id = service_id
		self._defw = defw_module
		self._peer_events = peer_events_module
		self._directory_getter = directory_getter or \
			(lambda: getattr(self._defw, "dirsvc", None))
		self._event_api = event_api_factory()
		self._recovery_timeout = recovery_timeout
		self._lock = threading.RLock()
		self._recovery_lock = threading.Lock()
		self._stop = threading.Event()
		self._directory = None
		self._directory_runtime_id = None
		self._registration_ids = []
		self._service_record = None
		self._runtime_id = None
		self._peer_handle = None
		self._generation = None
		self._clients = {}
		self._listeners = []
		self._event_thread = None
		self._recovery_thread = None
		self._started = False

	def start(self, directory=None, directory_runtime_id=None):
		with self._lock:
			if self._started:
				return self
			self._event_api.register_external()
			self._peer_events.add_peer_event_listener(
				self._handle_peer_event)
			self._started = True
			self._event_thread = threading.Thread(
				target=self._consume_events,
				name=f"qpm-lifecycle-{self.service_id}",
				daemon=True,
			)
			self._event_thread.start()
		try:
			self._bind_directory(
				directory or self._directory_getter(),
				directory_runtime_id=directory_runtime_id,
			)
		except Exception:
			self.close()
			raise
		return self

	def close(self):
		with self._lock:
			if not self._started:
				return
			self._started = False
			self._stop.set()
			directory = self._directory
			registration_ids = list(self._registration_ids)
			self._registration_ids = []
			self._service_record = None
			self._clients = {}
		try:
			self._peer_events.remove_peer_event_listener(
				self._handle_peer_event)
		except Exception:
			pass
		if directory is not None:
			for registration_id in registration_ids:
				try:
					directory.unregister_event_notification(
						registration_id)
				except Exception:
					pass
		try:
			self._event_api.unregister_external()
		except Exception:
			pass

	def api(self, binding_name, expected_runtime_id=None):
		return QPMManagedAPI(
			self,
			binding_name,
			expected_runtime_id=expected_runtime_id,
		)

	def client(self, binding_name, expected_runtime_id=None):
		with self._lock:
			self._require_runtime_locked(expected_runtime_id)
			client = self._clients.get(binding_name)
			if client is not None:
				return client
			record = copy.deepcopy(self._service_record)
			runtime_id = self._runtime_id
			peer_handle = self._peer_handle
		binding = self._select_api_binding(record, binding_name)
		client = self._defw.connect_to_binding({
			"service_record": record,
			"selected_binding": binding,
		})
		with self._lock:
			self._require_runtime_locked(expected_runtime_id)
			if (self._runtime_id != runtime_id or
					self._peer_handle != peer_handle):
				raise QPMBindingUnavailable(
					f"QPM {self.service_id!r} changed while connecting")
			self._clients.setdefault(binding_name, client)
			return self._clients[binding_name]

	def snapshot(self):
		with self._lock:
			return {
				"service_id": self.service_id,
				"directory_runtime_id": self._directory_runtime_id,
				"runtime_id": self._runtime_id,
				"peer_handle": self._peer_handle,
				"generation": self._generation,
				"available": self._service_record is not None,
			}

	def add_reconnect_listener(self, listener):
		if not callable(listener):
			raise QPMBindingError("QPM reconnect listener is not callable")
		with self._lock:
			if listener not in self._listeners:
				self._listeners.append(listener)
		return listener

	def remove_reconnect_listener(self, listener):
		with self._lock:
			if listener in self._listeners:
				self._listeners.remove(listener)

	def _bind_directory(self, directory, directory_runtime_id=None):
		if directory is None:
			raise QPMBindingUnavailable("DEFw directory service is unavailable")
		directory_runtime_id = directory_runtime_id or \
			self._current_directory_runtime_id()
		with self._lock:
			self._directory = directory
			self._directory_runtime_id = directory_runtime_id
			self._registration_ids = []
		registration_ids = []
		try:
			for event_type in (SERVICE_CONNECTED, SERVICE_DISCONNECTED):
				registration_ids.append(
					directory.register_event_notification(
						self._defw.me.my_endpoint(),
						event_type,
						self._event_api.class_id(),
						filters={"service_id": self.service_id},
					)
				)
			records = directory.resolve_services(
				service_id=self.service_id,
				service_type=QPM_SERVICE_TYPE,
			)
			record = self._one_service_record(records)
		except Exception:
			for registration_id in registration_ids:
				try:
					directory.unregister_event_notification(
						registration_id)
				except Exception:
					pass
			raise
		with self._lock:
			if self._directory is not directory:
				raise QPMBindingUnavailable(
					"directory changed while registering callbacks")
			self._registration_ids = registration_ids
		self._install_service_record(
			record,
			directory_runtime_id=directory_runtime_id,
		)

	def _one_service_record(self, records):
		service_records = {}
		for entry in records or []:
			record = entry.get("service_record", entry)
			if record.get("service_id") != self.service_id:
				continue
			identity = (
				record.get("runtime_id"),
				record.get("peer_handle"),
			)
			service_records[identity] = record
		if not service_records:
			raise QPMBindingUnavailable(
				f"QPM {self.service_id!r} is not registered")
		if len(service_records) != 1:
			raise QPMBindingError(
				f"QPM {self.service_id!r} has ambiguous registrations")
		return copy.deepcopy(next(iter(service_records.values())))

	def _select_api_binding(self, record, binding_name):
		if record is None:
			raise QPMBindingUnavailable(
				f"QPM {self.service_id!r} is unavailable")
		matches = [
			binding
			for binding in record.get("api_bindings", [])
			if binding.get("binding_name") == binding_name
		]
		if len(matches) != 1:
			raise QPMBindingError(
				f"QPM {self.service_id!r} does not expose one "
				f"{binding_name!r} binding")
		return copy.deepcopy(matches[0])

	def _require_runtime_locked(self, expected_runtime_id):
		if (expected_runtime_id is not None and self._runtime_id is not None and
				self._runtime_id != expected_runtime_id):
			raise QPMRuntimeChanged(
				f"QPM {self.service_id!r} restarted; a new reservation "
				"is required")
		if self._service_record is None:
			raise QPMBindingUnavailable(
				f"QPM {self.service_id!r} is unavailable")

	def _consume_events(self):
		while not self._stop.is_set():
			try:
				ready, _, _ = select.select(
					[self._event_api], [], [], 0.2)
				if not ready:
					continue
				for event in self._event_api.get():
					self._handle_service_event(event)
			except Exception:
				if not self._stop.is_set():
					import logging
					logging.exception(
						"QPM lifecycle event processing failed")

	def _handle_service_event(self, event):
		if not isinstance(event, dict):
			return
		if event.get("service_id") != self.service_id:
			return
		if not self._is_qpm_service_event(event):
			return
		with self._lock:
			if (self._directory_runtime_id and
					event.get("directory_runtime_id") !=
					self._directory_runtime_id):
				return
		if event.get("event") == SERVICE_CONNECTED:
			record = event.get("service_record")
			if isinstance(record, dict):
				self._install_service_record(
					record,
					directory_runtime_id=event.get(
						"directory_runtime_id"),
				)
		elif event.get("event") == SERVICE_DISCONNECTED:
			self._disconnect_service(event)

	def _is_qpm_service_event(self, event):
		service_type = event.get("service_type")
		if service_type is not None and service_type != QPM_SERVICE_TYPE:
			return False
		record = event.get("service_record")
		if isinstance(record, dict):
			record_service_type = record.get("service_type")
			if (record_service_type is not None and
					record_service_type != QPM_SERVICE_TYPE):
				return False
		return True

	def _disconnect_service(self, event):
		with self._lock:
			if (event.get("runtime_id") != self._runtime_id or
					event.get("peer_handle") != self._peer_handle):
				return
			self._service_record = None
			self._clients = {}

	def _install_service_record(self, record, directory_runtime_id=None):
		record = copy.deepcopy(record)
		if record.get("service_id") != self.service_id:
			raise QPMBindingError("QPM lifecycle event has wrong service_id")
		if not record.get("runtime_id") or not record.get("peer_handle"):
			raise QPMBindingError("QPM lifecycle event lacks runtime identity")
		with self._lock:
			old_runtime_id = self._runtime_id
			same_runtime = (
				old_runtime_id is None or
				old_runtime_id == record["runtime_id"]
			)
			self._directory_runtime_id = \
				directory_runtime_id or self._directory_runtime_id
			self._runtime_id = record["runtime_id"]
			self._peer_handle = record["peer_handle"]
			self._generation = record.get("generation")
			self._service_record = record
			self._clients = {}
			listeners = list(self._listeners)
		snapshot = self.snapshot()
		snapshot["same_runtime"] = same_runtime
		for listener in listeners:
			try:
				listener(dict(snapshot))
			except Exception:
				import logging
				logging.exception("QPM reconnect listener failed")

	def _handle_peer_event(self, event):
		try:
			if not self._peer_events.is_dirsvc_peer_event(event):
				return
		except Exception:
			return
		event_type = event.get("event_type")
		runtime_id = event.get("remote_runtime_id") or \
			event.get("runtime_id")
		if event_type in ("PEER_LOST", "PEER_REMOVED"):
			with self._lock:
				if (self._directory_runtime_id and runtime_id and
						self._directory_runtime_id != runtime_id):
					return
				self._directory = None
				self._registration_ids = []
				self._service_record = None
				self._clients = {}
		elif event_type == "PEER_READY":
			self._start_recovery(runtime_id)

	def _start_recovery(self, directory_runtime_id):
		with self._recovery_lock:
			if (self._recovery_thread is not None and
					self._recovery_thread.is_alive()):
				return
			self._recovery_thread = threading.Thread(
				target=self._recover_directory,
				args=(directory_runtime_id,),
				name=f"qpm-directory-recovery-{self.service_id}",
				daemon=True,
			)
			self._recovery_thread.start()

	def _recover_directory(self, directory_runtime_id):
		deadline = time.monotonic() + self._recovery_timeout
		while not self._stop.is_set() and time.monotonic() < deadline:
			directory = self._directory_getter()
			if directory is not None:
				try:
					self._bind_directory(
						directory,
						directory_runtime_id=directory_runtime_id,
					)
					return
				except Exception:
					pass
			time.sleep(0.1)

	def _current_directory_runtime_id(self):
		try:
			import defw_peers
			agent = defw_peers.get_dirsvc_agent()
			if agent is not None:
				return str(agent.get_remote_uuid())
		except Exception:
			pass
		return None
