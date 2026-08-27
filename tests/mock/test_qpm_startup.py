def site_qpm_record():
	return {
		"service_id": "qpm:iqm:site-a",
		"service_name": "QPM",
		"service_type": "qfw.qpm",
		"runtime_id": "qpm-runtime-1",
		"peer_handle": "qpm-peer-1",
		"endpoint": {
			"address": "qpm-host",
			"listen_port": 9020,
			"pid": 101,
			"node_name": "qpm-node",
			"hostname": "qpm-node.example",
			"runtime_id": "qpm-runtime-1",
		},
		"api_bindings": [{
			"binding_name": "execution",
			"client_module": "api_qpm_execution",
			"client_class": "QPMExecution",
			"service_module": "svc_iqm_qpm.svc_qpm",
			"service_class": "QPM",
			"version": 1,
		}],
		"selector": {
			"resources": ["IQM-20q"],
			"aliases": ["iqm"],
		},
		"properties": {
			"provider": "iqm",
			"qpm_type": 1,
			"qpm_capabilities": 4,
		},
		"qpm_type": 1,
		"qpm_capabilities": 4,
	}


class FakeSiteDirSvc:
	def __init__(self):
		self.registrations = []

	def register_service(self, service_ep, context=None):
		context = dict(context or {})
		self.registrations.append((service_ep, context))
		return [{
			"service_id": context["service_id"],
			"service_type": context["service_type"],
			"runtime_id": service_ep.get_id(),
			"peer_handle": service_ep.blk_uuid,
			"generation": 1,
		}]


class FakeEndpoint:
	def __init__(self):
		self.name = "qpm-node"
		self.hostname = "qpm-node.example"
		self.remote_uuid = "qpm-runtime-1"
		self.blk_uuid = "qpm-peer-1"

	def get_id(self):
		return self.remote_uuid


class FakeDefw:
	def __init__(self, dirsvc=None, site_ready=None, site_dirsvc=None,
		     records=None, listener_ready=True, controller_ready=True,
		     endpoint=None):
		self.dirsvc = dirsvc
		self.site_ready = set(site_ready or [])
		self.site_dirsvc = site_dirsvc
		self.records = list(records or [])
		self.listener_is_ready = listener_ready
		self.controller_is_ready = controller_ready
		self.endpoint = endpoint or FakeEndpoint()

	def connect_to_binding(self, binding):
		endpoint = binding["service_record"]["endpoint"]["address"]
		if endpoint not in self.site_ready:
			return None
		return self.site_dirsvc

	def qpm_site_service_records(self):
		return list(self.records)

	def qpm_site_registration_peer(self):
		return {
			"runtime_id": "qpm-runtime-1",
			"peer_handle": "qpm-peer-1",
			"endpoint": site_qpm_record()["endpoint"],
		}

	def qpm_site_registration_endpoint(self):
		return self.endpoint

	def qpm_listener_ready(self):
		return self.listener_is_ready

	def qpm_controller_ready(self):
		return self.controller_is_ready


def test_service_record_enriches_metadata_advertisement(monkeypatch):
	import util.qpm.startup as startup

	class Runtime:
		def __init__(self):
			self.me = self

		def my_endpoint(self):
			return FakeEndpoint()

	advertisement = site_qpm_record()
	advertisement.pop("runtime_id")
	advertisement.pop("peer_handle")
	advertisement.pop("endpoint")
	monkeypatch.setenv("QFW_QPM_SERVICE_ID", "qpm:iqm:configured")

	record = startup._service_record(Runtime(), advertisement)

	assert record["service_id"] == "qpm:iqm:configured"
	assert record["runtime_id"] == "qpm-runtime-1"
	assert record["peer_handle"] == "qpm-peer-1"
	assert record["service_type"] == "qfw.qpm"
	assert record["api_bindings"][0]["binding_name"] == "execution"
	assert record["capability"] == {}


def reset_qpm_state(uq):
	uq.qpm_initialized = False
	uq.qpm_shutdown = False


def test_qpm_startup_waits_for_dirsvc_by_default(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	started = []
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.setattr(
		startup,
		"_start_wait_thread",
		lambda defw_module, message, timeout=None: started.append(
			(defw_module, message, timeout)),
	)

	state = startup.initialize_qpm_service(FakeDefw(), "ready")

	assert state == "waiting-for-dirsvc"
	assert uq.qpm_initialized is False
	assert len(started) == 1


def test_qpm_startup_initializes_without_dirsvc_when_disabled(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	monkeypatch.setenv("QFW_QPM_REGISTER_WITH_DIRSVC", "no")
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)

	state = startup.initialize_qpm_service(FakeDefw(), "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True


def test_qpm_startup_direct_endpoint_skips_dirsvc(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", "yes")

	state = startup.initialize_qpm_service(FakeDefw(), "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True


def test_qpm_startup_long_running_listener_skips_allocation_dirsvc(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)

	state = startup.initialize_qpm_service(FakeDefw(), "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True


def test_qpm_startup_long_running_site_registration_waits(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	started = []
	site_dirsvc = FakeSiteDirSvc()
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
	monkeypatch.setattr(
		startup,
		"_start_wait_thread",
		lambda defw_module, message, timeout=None: started.append(
			(defw_module, message, timeout)),
	)

	state = startup.initialize_qpm_service(
		FakeDefw(
			site_ready=set(),
			site_dirsvc=site_dirsvc,
			records=[site_qpm_record()],
		),
		"ready",
	)

	assert state == "waiting-for-dirsvc"
	assert uq.qpm_initialized is False
	assert len(started) == 1
	assert site_dirsvc.registrations == []


def test_qpm_startup_long_running_site_registration_registers_payload(
		monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	site_dirsvc = FakeSiteDirSvc()
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")

	state = startup.initialize_qpm_service(
		FakeDefw(
			site_ready={"site-a"},
			site_dirsvc=site_dirsvc,
			records=[site_qpm_record()],
		),
		"ready",
	)

	assert state == "initialized"
	assert uq.qpm_initialized is True
	assert len(site_dirsvc.registrations) == 1
	registered_endpoint, context = site_dirsvc.registrations[0]
	assert registered_endpoint.get_id() == "qpm-runtime-1"
	assert context["service_type"] == "qfw.qpm"
	assert context["selector"]["resources"] == ["IQM-20q"]
	assert context["properties"]["provider"] == "iqm"
	assert context["api_bindings"][0]["binding_name"] == "execution"
	assert context["api_bindings"][0]["client_class"] == "QPMExecution"

	config = startup.startup_config()
	assert config["operation_mode"] == "long-running"
	assert config["register_with_dirsvc"] is True


def test_qpm_startup_long_running_site_registration_uses_defw_api(
		monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	site_dirsvc = FakeSiteDirSvc()
	endpoint = FakeEndpoint()
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")

	state = startup.initialize_qpm_service(
		FakeDefw(
			site_ready={"site-a"},
			site_dirsvc=site_dirsvc,
			records=[site_qpm_record()],
			endpoint=endpoint,
		),
		"ready",
	)

	assert state == "initialized"
	assert uq.qpm_initialized is True
	assert len(site_dirsvc.registrations) == 1
	registered_endpoint, context = site_dirsvc.registrations[0]
	assert registered_endpoint is endpoint
	assert context["service_id"] == "qpm:iqm:site-a"
	assert context["service_type"] == "qfw.qpm"
	assert context["selector"]["aliases"] == ["iqm"]
	assert context["api_bindings"][0]["binding_name"] == "execution"
	assert context["properties"]["provider"] == "iqm"


def test_qpm_startup_registration_records_lifecycle_telemetry(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq
	from util.qpm.controller import (
		clear_target_controllers,
		controller_config,
		get_target_controller,
	)

	reset_qpm_state(uq)
	clear_target_controllers()
	site_dirsvc = FakeSiteDirSvc()
	controller = get_target_controller(
		controller_config(None, target_id="startup-target"),
		1,
		admission_context_factory=lambda threading_mode: object(),
		scheduler_context_factory=(
			lambda threading_mode, target_id=None: object()),
	)
	record = site_qpm_record()
	record["properties"]["controller"] = {"target_id": "startup-target"}
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")

	state = startup.initialize_qpm_service(
		FakeDefw(
			site_ready={"site-a"},
			site_dirsvc=site_dirsvc,
			records=[record],
		),
		"ready",
	)
	telemetry = controller.service_lifecycle_telemetry()
	registration = next(
		item for item in telemetry["lifecycle_events"]
		if item["event"] == "service-registration")

	assert state == "initialized"
	assert registration["source"] == "defw-directory"
	assert registration["details"]["service_id"] == "qpm:iqm:site-a"
	assert registration["details"]["directory_endpoint"] == "site-a"


def test_qpm_startup_records_real_defw_directory_lifecycle(monkeypatch):
	import time

	import defw_directory
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq
	from util.qpm.controller import (
		clear_target_controllers,
		controller_config,
		get_target_controller,
	)

	reset_qpm_state(uq)
	clear_target_controllers()
	controller = get_target_controller(
		controller_config(None, target_id="directory-target"),
		1,
		admission_context_factory=lambda threading_mode: object(),
		scheduler_context_factory=(
			lambda threading_mode, target_id=None: object()),
	)
	directory = defw_directory.Directory(retention_seconds=0.01)
	monkeypatch.setattr(defw_directory, "directory", directory)
	record = site_qpm_record()
	record["properties"]["controller"] = {"target_id": "directory-target"}
	monkeypatch.delenv("QFW_QPM_OPERATION_MODE", raising=False)
	monkeypatch.setenv("QFW_QPM_REGISTER_WITH_DIRSVC", "no")
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)

	state = startup.initialize_qpm_service(
		FakeDefw(dirsvc=object()),
		"ready",
	)
	registered = defw_directory.register_service(record)
	defw_directory.apply_peer_event({
		"event_type": "PEER_LOST",
		"peer_handle": registered["peer_handle"],
		"remote_runtime_id": registered["runtime_id"],
		"reason": "heartbeat-timeout",
		"timestamp": time.time() + 1,
	})
	restart = site_qpm_record()
	restart["runtime_id"] = "qpm-runtime-2"
	restart["peer_handle"] = "qpm-peer-2"
	restart["endpoint"]["runtime_id"] = "qpm-runtime-2"
	restart["properties"]["controller"] = {"target_id": "directory-target"}
	restarted = defw_directory.register_service(restart)
	defw_directory.deregister_service(
		restarted["service_id"],
		restarted["runtime_id"],
		restarted["generation"],
	)
	defw_directory.purge_expired(now=time.time() + 2)

	telemetry = controller.service_lifecycle_telemetry()
	events = [record["event"] for record in telemetry["lifecycle_events"]]
	audit_events = [record["event"] for record in telemetry["audit_records"]]
	peer_lost = next(
		record for record in telemetry["lifecycle_events"]
		if record["event"] == "peer-lost")
	generation_change = next(
		record for record in telemetry["lifecycle_events"]
		if record["event"] == "generation-change")

	assert state == "initialized"
	for event in (
			"service-registration",
			"peer-lost",
			"service-timeout",
			"service-restart",
			"generation-change",
			"service-deregistration",
			"retention-purge"):
		assert event in events
		assert event in audit_events
	assert peer_lost["reason"] == "heartbeat-timeout"
	assert generation_change["details"]["previous_generation"] == 1
	assert generation_change["details"]["current_generation"] == 2


def test_qpm_startup_site_registration_waits_for_listener_before_registering(
		monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	started = []
	site_dirsvc = FakeSiteDirSvc()
	fake_defw = FakeDefw(
		site_ready={"site-a"},
		site_dirsvc=site_dirsvc,
		records=[site_qpm_record()],
		listener_ready=False,
	)
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
	monkeypatch.setattr(
		startup,
		"_start_wait_thread",
		lambda defw_module, message, timeout=None: started.append(
			(defw_module, message, timeout)),
	)

	state = startup.initialize_qpm_service(fake_defw, "ready")

	assert state == "waiting-for-listener"
	assert uq.qpm_initialized is False
	assert len(started) == 1
	assert site_dirsvc.registrations == []

	fake_defw.listener_is_ready = True
	state = startup.initialize_qpm_service(fake_defw, "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True
	assert len(site_dirsvc.registrations) == 1


def test_qpm_startup_site_registration_waits_for_controller_before_registering(
		monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	started = []
	site_dirsvc = FakeSiteDirSvc()
	fake_defw = FakeDefw(
		site_ready={"site-a"},
		site_dirsvc=site_dirsvc,
		records=[site_qpm_record()],
		controller_ready=False,
	)
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
	monkeypatch.setattr(
		startup,
		"_start_wait_thread",
		lambda defw_module, message, timeout=None: started.append(
			(defw_module, message, timeout)),
	)

	state = startup.initialize_qpm_service(fake_defw, "ready")
	assert state == "waiting-for-controller"
	assert uq.qpm_initialized is False
	assert len(started) == 1
	assert startup.controller_ready(fake_defw) is False
	assert site_dirsvc.registrations == []

	fake_defw.controller_is_ready = True
	state = startup.initialize_qpm_service(fake_defw, "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True
	assert len(site_dirsvc.registrations) == 1


def test_qpm_startup_direct_endpoint_reports_listener_health(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	started = []
	fake_defw = FakeDefw(listener_ready=False, controller_ready=True)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", "yes")
	monkeypatch.setenv("QFW_DIRECT_QPM_ENDPOINT", "qpm-direct:9000")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)
	monkeypatch.setattr(
		startup,
		"_start_wait_thread",
		lambda defw_module, message, timeout=None: started.append(
			(defw_module, message, timeout)),
	)

	state = startup.initialize_qpm_service(fake_defw, "ready")
	config = startup.startup_config()

	assert state == "waiting-for-listener"
	assert uq.qpm_initialized is False
	assert len(started) == 1
	assert config["direct_endpoint_enabled"] is True
	assert config["direct_qpm_endpoint"] == "qpm-direct:9000"
	assert config["register_with_dirsvc"] is False
	assert startup.listener_ready(fake_defw) is False
	assert startup.controller_ready(fake_defw) is True


def test_qpm_startup_wait_for_startup_times_out(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")

	startup.wait_for_startup(FakeDefw(), "ready", timeout=0)

	assert uq.qpm_initialized is False
