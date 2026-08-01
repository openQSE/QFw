class FakeDefw:
	def __init__(self, resmgr=None, site_ready=None):
		self.resmgr = resmgr
		self.site_ready = set(site_ready or [])

	def site_dirsvc_ready(self, endpoint):
		return endpoint in self.site_ready


def reset_qpm_state(uq):
	uq.qpm_initialized = False
	uq.qpm_shutdown = False


def test_qpm_startup_waits_for_dirsvc_by_default(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	started = []
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", raising=False)
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
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", raising=False)

	state = startup.initialize_qpm_service(FakeDefw(), "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True


def test_qpm_startup_direct_endpoint_fallback_skips_dirsvc(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", "yes")

	state = startup.initialize_qpm_service(FakeDefw(), "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True


def test_qpm_startup_long_running_listener_skips_allocation_dirsvc(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", raising=False)
	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)

	state = startup.initialize_qpm_service(FakeDefw(), "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True


def test_qpm_startup_long_running_site_registration_waits(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	started = []
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
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


def test_qpm_startup_long_running_site_registration_uses_site_ready(
		monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_REGISTER_WITH_DIRSVC", raising=False)
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")

	state = startup.initialize_qpm_service(
		FakeDefw(site_ready={"site-a"}), "ready")

	assert state == "initialized"
	assert uq.qpm_initialized is True


def test_qpm_startup_wait_for_dirsvc_times_out(monkeypatch):
	import util.qpm.startup as startup
	import util.qpm.util_qpm as uq

	reset_qpm_state(uq)
	monkeypatch.setenv("QFW_QPM_OPERATION_MODE", "long-running")
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", raising=False)
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")

	startup.wait_for_dirsvc(FakeDefw(), "ready", timeout=0)

	assert uq.qpm_initialized is False
