from tests.mock.fakes import (
	FakeDefwModule,
	FakeQPM,
	FakeResourceManager,
	FakeServiceInfo,
)


def test_get_qpm_returns_reserved_service(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM()
	service_info = FakeServiceInfo(
		fake_qpm,
		properties={"provider": "iqm", "service_type": "qfw.qpm"},
	)
	rmgr = FakeResourceManager([service_info])
	fake_defw = FakeDefwModule()

	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is fake_qpm
	assert rmgr.requests == [("QPM", 4, 2)]
	assert fake_defw.connections == [([service_info], "QPM")]
	assert fake_qpm.shutdown_called is False


def test_get_qpm_shuts_down_failed_service_probe(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM(test_error=RuntimeError("probe failed"))
	service_info = FakeServiceInfo(
		fake_qpm,
		properties={"provider": "iqm", "service_type": "qfw.qpm"},
	)
	rmgr = FakeResourceManager([service_info])
	fake_defw = FakeDefwModule()

	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is fake_qpm
	assert fake_qpm.shutdown_called is True


def test_get_qpm_propagates_reservation_failures(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	class FailingResourceManager:
		def get_services(self, *args, **kwargs):
			raise RuntimeError("reserve failed")

	monkeypatch.setattr(
		lookup_service,
		"defw_get_resource_mgr",
		lambda: FailingResourceManager(),
	)

	try:
		lookup_service.get_qpm(qpm_type=4, qpm_cap=2)
	except RuntimeError as exc:
		assert str(exc) == "reserve failed"
	else:
		raise AssertionError("expected reservation failure to propagate")


def test_get_qpm_selects_requested_provider(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	native_qpm = FakeQPM()
	shim_qpm = FakeQPM()
	native = FakeServiceInfo(
		native_qpm,
		endpoint="native",
		properties={"provider": "iqm", "service_type": "qfw.qpm"},
	)
	shim = FakeServiceInfo(
		shim_qpm,
		endpoint="shim",
		properties={"provider": "shim", "service_type": "qfw.qpm"},
	)
	rmgr = FakeResourceManager([native, shim])
	fake_defw = FakeDefwModule()

	monkeypatch.setenv("QFW_QPM_IMPL", "shim")
	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is shim_qpm
	assert fake_defw.connections == [([shim], "QPM")]


def test_get_qpm_rejects_unavailable_requested_provider(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service
	from qfw_qiskit.qpm_resolver import QPMProviderPolicyError

	shim = FakeServiceInfo(
		FakeQPM(),
		endpoint="shim",
		properties={"provider": "shim", "service_type": "qfw.qpm"},
	)
	rmgr = FakeResourceManager([shim])
	fake_defw = FakeDefwModule()

	monkeypatch.setenv("QFW_QPM_IMPL", "iqm")
	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: rmgr)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	try:
		lookup_service.get_qpm(qpm_type=4, qpm_cap=2)
	except QPMProviderPolicyError as exc:
		assert "provider 'iqm'" in str(exc)
	else:
		raise AssertionError("expected requested provider policy failure")

	assert fake_defw.connections == []


def test_resolver_rejects_ambiguous_requested_provider():
	from qfw_qiskit.qpm_resolver import (
		QPMAmbiguousResolutionError,
		QPMResolver,
	)

	first = FakeServiceInfo(
		FakeQPM(),
		endpoint="iqm-a",
		properties={"provider": "iqm", "service_type": "qfw.qpm"},
	)
	second = FakeServiceInfo(
		FakeQPM(),
		endpoint="iqm-b",
		properties={"provider": "iqm", "service_type": "qfw.qpm"},
	)
	resolver = QPMResolver.from_resource_manager(
		FakeResourceManager([first, second]),
		defw_module=FakeDefwModule(),
		sleeper=lambda seconds: None,
	)

	try:
		resolver.resolve(
			service_type="qfw.qpm", provider="iqm", timeout=1)
	except QPMAmbiguousResolutionError as exc:
		assert "provider 'iqm'" in str(exc)
		assert "iqm-a" in str(exc)
		assert "iqm-b" in str(exc)
	else:
		raise AssertionError("expected ambiguous provider resolution")


def test_resolver_normalizes_directory_service_records():
	from qfw_qiskit.qpm_resolver import DirectoryScope, QPMResolver

	class DirectoryClient:
		def resolve_service(self, **kwargs):
			self.kwargs = kwargs
			return {
				"directory_scope": "site",
				"directory_identity": "site-dir-a",
				"service_record": {
					"service_id": "qpm-iqm-1",
					"service_name": "QPM",
					"service_type": "qfw.qpm",
					"runtime_id": "runtime-1",
					"generation": 3,
					"endpoint": {"address": "qpm.example", "listen_port": 9020},
					"selector": {
						"resources": ["IQM-20q"],
						"aliases": ["ornl-iqm"],
					},
				},
				"selected_api_binding": {
					"binding_name": "execution",
					"client_module": "api_qpm_execution",
					"client_class": "QPMExecution",
					"service_module": "svc_iqm_qpm.svc_qpm",
					"service_class": "QPM",
					"version": 2,
				},
			}

	class Connector:
		def connect(self, resolved):
			self.resolved = resolved
			return "proxy"

	directory = DirectoryClient()
	connector = Connector()
	resolver = QPMResolver(
		[DirectoryScope("site", "site", client=directory, priority=50)],
		connector=connector,
		sleeper=lambda seconds: None,
	)

	proxy = resolver.connect(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		timeout=1,
	)

	assert proxy == "proxy"
	assert directory.kwargs["binding_name"] == "execution"
	assert connector.resolved.service_id == "qpm-iqm-1"
	assert connector.resolved.runtime_id == "runtime-1"
	assert connector.resolved.generation == 3
	assert connector.resolved.directory_scope == "site"
	assert connector.resolved.directory_identity == "site-dir-a"
	assert connector.resolved.api_binding.client_class == "QPMExecution"
