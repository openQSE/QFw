import copy

from tests.mock.fakes import FakeQPM
from api_qpm import QPMCapability, QPMType


DEFAULT_QPM_TYPE = QPMType.QPM_TYPE_HARDWARE
DEFAULT_QPM_CAPABILITIES = QPMCapability.QPM_CAP_SUPERCONDUCTING
QPM_BINDINGS = (
	{
		"binding_name": "default",
		"client_module": "api_qpm",
		"client_class": "QPM",
		"service_module": "svc_iqm_qpm.svc_qpm",
		"service_class": "QPM",
		"version": 1,
	},
	{
		"binding_name": "execution",
		"client_module": "api_qpm_execution",
		"client_class": "QPMExecution",
		"service_module": "svc_iqm_qpm.svc_qpm",
		"service_class": "QPM",
		"version": 1,
	},
)


def qpm_directory_record(service_id, fake_qpm, *, provider="iqm",
			 endpoint=None):
	bindings = [
		{**binding, "service_module": f"svc_{provider}_qpm.svc_qpm"}
		for binding in QPM_BINDINGS
	]
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
				"qpm_type": int(DEFAULT_QPM_TYPE),
				"qpm_capabilities": int(DEFAULT_QPM_CAPABILITIES),
			},
			"api_bindings": bindings,
		},
		"selected_binding": bindings[1],
	}


class FakeDirectoryService:
	def __init__(self, records):
		self.records = list(records)
		self.queries = []

	def resolve_services(self, **kwargs):
		self.queries.append(kwargs)
		results = []
		for record in self.records:
			result = {
				key: copy.deepcopy(value)
				for key, value in record.items()
				if key != "fake_qpm"
			}
			binding_name = kwargs.get("binding_name")
			if binding_name:
				for binding in result["service_record"].get(
						"api_bindings", []):
					if binding.get("binding_name") == binding_name:
						result["selected_binding"] = binding
						break
			results.append(result)
		return results


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

	result = lookup_service.get_qpm(
		qpm_type=DEFAULT_QPM_TYPE,
		qpm_capabilities=DEFAULT_QPM_CAPABILITIES,
	)

	assert result is fake_qpm
	assert len(dirsvc.queries) == 1
	assert dirsvc.queries[0]["service_name"] == "QPM"
	assert dirsvc.queries[0]["service_type"] == "qfw.qpm"
	assert dirsvc.queries[0]["binding_name"] == "default"
	assert dirsvc.queries[0]["qpm_type"] == DEFAULT_QPM_TYPE
	assert dirsvc.queries[0]["qpm_capability"] == DEFAULT_QPM_CAPABILITIES
	assert dirsvc.queries[0]["qpm_capabilities"] == DEFAULT_QPM_CAPABILITIES
	assert len(fake_defw.binding_connections) == 1
	binding = fake_defw.binding_connections[0]
	assert binding["service_record"]["service_id"] == "qpm-iqm"
	assert binding["selected_binding"]["binding_name"] == "default"
	assert fake_qpm.shutdown_called is False


def test_get_qpm_leaves_failed_service_probe_running(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM(test_error=RuntimeError("probe failed"))
	record = qpm_directory_record("qpm-iqm", fake_qpm)
	dirsvc = FakeDirectoryService([record])
	fake_defw = BindingDefwModule([record])

	monkeypatch.setattr(
		lookup_service, "defw_get_directory_service", lambda: dirsvc)
	monkeypatch.setattr(lookup_service, "defw", fake_defw)

	result = lookup_service.get_qpm(
		qpm_type=DEFAULT_QPM_TYPE,
		qpm_capabilities=DEFAULT_QPM_CAPABILITIES,
	)

	assert result is fake_qpm
	assert fake_qpm.shutdown_called is False


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
		lookup_service.get_qpm(
			qpm_type=DEFAULT_QPM_TYPE,
			qpm_capabilities=DEFAULT_QPM_CAPABILITIES,
		)
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

	result = lookup_service.get_qpm(
		qpm_type=DEFAULT_QPM_TYPE,
		qpm_capabilities=DEFAULT_QPM_CAPABILITIES,
	)

	assert result is fake_qpm
	assert len(fake_defw.binding_connections) == 1
	binding = fake_defw.binding_connections[0]
	assert binding["service_record"]["endpoint"]["address"] == "qpm-direct"
	assert binding["service_record"]["endpoint"]["listen_port"] == 9000
	assert binding["selected_binding"]["binding_name"] == "default"


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

	result = lookup_service.get_qpm(
		qpm_type=DEFAULT_QPM_TYPE,
		qpm_capabilities=DEFAULT_QPM_CAPABILITIES,
	)

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
		lookup_service.get_qpm(
			qpm_type=DEFAULT_QPM_TYPE,
			qpm_capabilities=DEFAULT_QPM_CAPABILITIES,
		)
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
