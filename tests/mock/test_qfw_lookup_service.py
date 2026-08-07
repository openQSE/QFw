from tests.mock.fakes import FakeQPM


def qpm_directory_record(service_id, fake_qpm, *, provider="iqm",
			 endpoint=None):
	return {
		"fake_qpm": fake_qpm,
		"service_record": {
			"service_id": service_id,
			"service_name": "QPM",
			"service_type": "qfw.qpm",
			"runtime_id": f"{service_id}-runtime",
			"generation": 1,
			"endpoint": endpoint or f"{service_id}:9000",
			"selector": {
				"resources": ["IQM-20q"],
				"aliases": [provider],
			},
			"properties": {
				"provider": provider,
				"legacy_type": 4,
				"legacy_capabilities": 2,
			},
		},
		"selected_binding": {
			"binding_name": "execution",
			"client_module": "api_qpm_execution",
			"client_class": "QPMExecution",
			"service_module": f"svc_{provider}_qpm.svc_qpm",
			"service_class": "QPM",
			"version": 1,
		},
	}


class FakeDirectoryService:
	def __init__(self, records):
		self.records = list(records)
		self.queries = []

	def resolve_services(self, **kwargs):
		self.queries.append(kwargs)
		return [
			{key: value for key, value in record.items()
			 if key != "fake_qpm"}
			for record in self.records
		]


class BindingDefwModule:
	def __init__(self, records=None, default_qpm=None):
		self.binding_connections = []
		self.qpms = {
			record["service_record"]["service_id"]: record["fake_qpm"]
			for record in records or []
		}
		self.default_qpm = default_qpm

	def connect_to_binding(self, resolved_binding):
		self.binding_connections.append(resolved_binding)
		service_id = resolved_binding["service_record"]["service_id"]
		return self.qpms.get(service_id, self.default_qpm)


def test_get_qpm_uses_allocation_dirsvc_selected_binding(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM()
	record = qpm_directory_record("qpm-iqm", fake_qpm)
	dirsvc = FakeDirectoryService([record])
	fake_defw = BindingDefwModule([record])

	monkeypatch.setattr(
		lookup_service, "defw_get_directory_service", lambda: dirsvc)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is fake_qpm
	assert len(dirsvc.queries) == 1
	assert dirsvc.queries[0]["service_name"] == "QPM"
	assert dirsvc.queries[0]["service_type"] == "qfw.qpm"
	assert dirsvc.queries[0]["binding_name"] == "execution"
	assert dirsvc.queries[0]["svc_type"] == 4
	assert dirsvc.queries[0]["svc_caps"] == 2
	assert len(fake_defw.binding_connections) == 1
	binding = fake_defw.binding_connections[0]
	assert binding["service_record"]["service_id"] == "qpm-iqm"
	assert binding["selected_binding"]["binding_name"] == "execution"
	assert fake_qpm.shutdown_called is False


def test_get_qpm_shuts_down_failed_service_probe(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM(test_error=RuntimeError("probe failed"))
	record = qpm_directory_record("qpm-iqm", fake_qpm)
	dirsvc = FakeDirectoryService([record])
	fake_defw = BindingDefwModule([record])

	monkeypatch.setattr(
		lookup_service, "defw_get_directory_service", lambda: dirsvc)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is fake_qpm
	assert fake_qpm.shutdown_called is True


def test_get_qpm_propagates_directory_failures(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	class FailingDirectory:
		def resolve_services(self, **kwargs):
			raise RuntimeError("directory lookup failed")

	monkeypatch.setattr(
		lookup_service,
		"defw_get_directory_service",
		lambda: FailingDirectory(),
	)

	try:
		lookup_service.get_qpm(qpm_type=4, qpm_cap=2)
	except RuntimeError as exc:
		assert str(exc) == "directory lookup failed"
	else:
		raise AssertionError("expected directory lookup failure to propagate")


def test_get_qpm_uses_direct_endpoint_without_allocation_directory(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM()
	fake_defw = BindingDefwModule(default_qpm=fake_qpm)

	def unavailable_directory_service():
		raise RuntimeError("allocation-local directory service unavailable")

	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)
	monkeypatch.delenv("QFW_QPM_IMPL", raising=False)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", "yes")
	monkeypatch.setenv("QFW_DIRECT_QPM_ENDPOINT", "qpm-direct:9000")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "direct")
	monkeypatch.setattr(
		lookup_service, "defw_get_directory_service",
		unavailable_directory_service)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is fake_qpm
	assert len(fake_defw.binding_connections) == 1
	binding = fake_defw.binding_connections[0]
	assert binding["service_record"]["endpoint"]["address"] == "qpm-direct"
	assert binding["service_record"]["endpoint"]["listen_port"] == 9000
	assert binding["selected_binding"]["binding_name"] == "execution"


def test_get_qpm_selects_requested_provider(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	native_qpm = FakeQPM()
	shim_qpm = FakeQPM()
	native = qpm_directory_record("native", native_qpm, provider="iqm")
	shim = qpm_directory_record("shim", shim_qpm, provider="shim")
	dirsvc = FakeDirectoryService([native, shim])
	fake_defw = BindingDefwModule([native, shim])

	monkeypatch.setenv("QFW_QPM_IMPL", "shim")
	monkeypatch.setattr(
		lookup_service, "defw_get_directory_service", lambda: dirsvc)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is shim_qpm
	assert [
		item["service_record"]["service_id"]
		for item in fake_defw.binding_connections
	] == ["shim"]


def test_get_qpm_rejects_unavailable_requested_provider(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service
	from qfw_qiskit.qpm_resolver import QPMProviderPolicyError

	shim = qpm_directory_record("shim", FakeQPM(), provider="shim")
	dirsvc = FakeDirectoryService([shim])
	fake_defw = BindingDefwModule([shim])

	monkeypatch.setenv("QFW_QPM_IMPL", "iqm")
	monkeypatch.setattr(
		lookup_service, "defw_get_directory_service", lambda: dirsvc)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	try:
		lookup_service.get_qpm(qpm_type=4, qpm_cap=2)
	except QPMProviderPolicyError as exc:
		assert "provider 'iqm'" in str(exc)
	else:
		raise AssertionError("expected requested provider policy failure")

	assert fake_defw.binding_connections == []


def test_resolver_rejects_ambiguous_requested_provider():
	from qfw_qiskit.qpm_resolver import (
		DirectoryScope,
		QPMAmbiguousResolutionError,
		QPMResolver,
	)

	first = qpm_directory_record("iqm-a", FakeQPM(), provider="iqm")
	second = qpm_directory_record("iqm-b", FakeQPM(), provider="iqm")
	resolver = QPMResolver(
		[DirectoryScope(
			"allocation-local",
			"allocation-local",
			client=FakeDirectoryService([first, second]),
			priority=100,
		)],
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
