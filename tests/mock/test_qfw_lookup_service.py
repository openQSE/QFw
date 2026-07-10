import pytest

from tests.mock.fakes import FakeQPM


class FakeServiceInfo:
	def __init__(self, provider):
		self._provider = provider

	def get_properties(self):
		return {"provider": self._provider}


class FakeResourceMgr:
	def __init__(self, services):
		self._services = services
		self.calls = []

	def get_services(self, name, qpm_type, qpm_cap):
		self.calls.append((name, qpm_type, qpm_cap))
		return list(self._services)


class ConnectRecorder:
	"""Stand-in for defw.connect_to_resource that records the chosen services."""

	def __init__(self, qpm):
		self._qpm = qpm
		self.services = None
		self.name = None

	def __call__(self, services, name):
		self.services = services
		self.name = name
		return [self._qpm]


def test_get_qpm_returns_reserved_service(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM()
	info = FakeServiceInfo("iqm")
	rmgr = FakeResourceMgr([info])
	connect = ConnectRecorder(fake_qpm)

	monkeypatch.delenv("QFW_QPM_IMPL", raising=False)  # default impl == iqm
	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	monkeypatch.setattr(lookup_service.defw, "connect_to_resource", connect)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is fake_qpm
	assert fake_qpm.shutdown_called is False
	assert rmgr.calls == [("QPM", 4, 2)]
	assert connect.services == [info]
	assert connect.name == "QPM"


def test_get_qpm_selects_impl_by_provider(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	iqm_info = FakeServiceInfo("iqm")
	shim_info = FakeServiceInfo("shim")
	rmgr = FakeResourceMgr([iqm_info, shim_info])
	connect = ConnectRecorder(FakeQPM())

	# resmgr matching ignores the 'provider' property, so both come back; the
	# QFW_QPM_IMPL selection must pick the requested one.
	monkeypatch.setenv("QFW_QPM_IMPL", "shim")
	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	monkeypatch.setattr(lookup_service.defw, "connect_to_resource", connect)

	lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert connect.services == [shim_info]


def test_get_qpm_falls_back_when_requested_impl_absent(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	iqm_info = FakeServiceInfo("iqm")
	rmgr = FakeResourceMgr([iqm_info])
	connect = ConnectRecorder(FakeQPM())

	# No QPM advertises provider 'shim'; fall back to whatever matched.
	monkeypatch.setenv("QFW_QPM_IMPL", "shim")
	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	monkeypatch.setattr(lookup_service.defw, "connect_to_resource", connect)

	lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert connect.services == [iqm_info]


def test_get_qpm_raises_and_shuts_down_on_probe_failure(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM(test_error=RuntimeError("probe failed"))
	rmgr = FakeResourceMgr([FakeServiceInfo("iqm")])

	monkeypatch.delenv("QFW_QPM_IMPL", raising=False)
	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	monkeypatch.setattr(
		lookup_service.defw, "connect_to_resource", ConnectRecorder(fake_qpm))

	# A failed probe must not hand back a shut-down handle: it tears the QPM
	# down and propagates the failure.
	with pytest.raises(RuntimeError, match="probe failed"):
		lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert fake_qpm.shutdown_called is True


def test_get_qpm_raises_when_no_qpm_available(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service
	from defw_exception import DEFwReserveError

	rmgr = FakeResourceMgr([])  # resmgr never returns a QPM

	monkeypatch.delenv("QFW_QPM_IMPL", raising=False)
	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	# Don't actually sleep through the connect-retry loop.
	monkeypatch.setattr(lookup_service, "sleep", lambda _seconds: None)

	with pytest.raises(DEFwReserveError):
		lookup_service.get_qpm(qpm_type=4, qpm_cap=2)
