from defw_exception import DEFwReserveError

from qfw_qiskit.qpm_resolver import (
	DEFwDirectoryClient,
	DEFwQPMConnector,
	DirectoryScope,
	QPMAmbiguousResolutionError,
	QPMInvalidDirectoryRecordError,
	QPMResolver,
	QPMSimulatorFallbackPolicyError,
	QPMStaleGenerationError,
	QPMUnsupportedConfigurationError,
	binding_name_for_category,
)


def directory_record(service_id, *, generation=1):
	return {
		"service_record": {
			"service_id": service_id,
			"service_name": "QPM",
			"service_type": "qfw.qpm",
			"runtime_id": f"{service_id}-runtime",
			"generation": generation,
			"endpoint": service_id,
			"selector": {
				"resources": ["IQM-20q"],
			},
		},
		"selected_api_binding": {
			"binding_name": "execution",
			"client_class": "QPM",
			"service_class": "QPM",
		},
	}


class DirectoryClient:
	def __init__(self, records, latest_generation=None):
		self.records = list(records)
		self.latest_generation = latest_generation
		self.queries = []

	def resolve_service(self, **kwargs):
		self.kwargs = kwargs
		self.queries.append(kwargs)
		return list(self.records)

	def get_service_generation(self, service_id):
		return self.latest_generation


class Connector:
	def __init__(self):
		self.connected = []

	def connect(self, resolved):
		self.connected.append(resolved)
		return resolved


class FakeQPMApi:
	def reserve(self, request):
		return {
			"operation": "reserve",
			"status": "accepted",
			"request": dict(request),
		}

	def release(self, reservation_id):
		return {
			"operation": "release",
			"status": "released",
			"reservation_id": reservation_id,
		}

	def sync_run(self, info, **kwargs):
		return {
			"operation": "sync_run",
			"status": "completed",
			"info": dict(info),
			"context": dict(kwargs),
		}

	def async_run(self, info, **kwargs):
		return {
			"operation": "async_run",
			"status": "submitted",
			"cid": "cid-1",
			"info": dict(info),
			"context": dict(kwargs),
		}


class BindingDefw:
	def __init__(self, qpm_api):
		self.qpm_api = qpm_api
		self.binding_connections = []

	def connect_to_binding(self, resolved_binding):
		self.binding_connections.append(resolved_binding)
		return self.qpm_api


def test_resolver_prefers_configured_directory_scope_order():
	local = DirectoryClient([directory_record("local-qpm")])
	site = DirectoryClient([directory_record("site-qpm")])
	resolver = QPMResolver(
		[
			DirectoryScope("local", "allocation-local", client=local,
				       priority=100),
			DirectoryScope("site", "site", client=site, priority=50),
		],
		connector=Connector(),
		selection_order=["site", "allocation-local"],
		sleeper=lambda seconds: None,
	)

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		timeout=1,
	)

	assert resolved.service_id == "site-qpm"
	assert resolved.directory_scope == "site"


def test_resolver_reports_ambiguous_same_rank_candidates():
	site_a = DirectoryClient([directory_record("site-a-qpm")])
	site_b = DirectoryClient([directory_record("site-b-qpm")])
	resolver = QPMResolver(
		[
			DirectoryScope("site-a", "site", client=site_a, priority=50),
			DirectoryScope("site-b", "site", client=site_b, priority=50),
		],
		connector=Connector(),
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	try:
		resolver.resolve(
			service_type="qfw.qpm",
			selector_resource="IQM-20q",
			api_category="execution",
			timeout=1,
		)
	except QPMAmbiguousResolutionError as exc:
		assert "site-a-qpm" in str(exc)
		assert "site-b-qpm" in str(exc)
	else:
		raise AssertionError("expected ambiguous QPM resolution")


def test_resolver_rejects_stale_generation_before_connecting():
	directory = DirectoryClient(
		[directory_record("qpm-iqm", generation=1)],
		latest_generation=2,
	)
	connector = Connector()
	resolver = QPMResolver(
		[DirectoryScope("site", "site", client=directory, priority=50)],
		connector=connector,
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	try:
		resolver.connect(
			service_type="qfw.qpm",
			selector_resource="IQM-20q",
			api_category="execution",
			timeout=1,
		)
	except QPMStaleGenerationError as exc:
		assert "generation 1 is older than 2" in str(exc)
	else:
		raise AssertionError("expected stale generation rejection")

	assert connector.connected == []


def test_resolver_from_environment_selects_site_scoped_directory(monkeypatch):
	clients = {}

	def client_factory(endpoint):
		client = DirectoryClient([directory_record(f"{endpoint}-qpm")])
		clients[endpoint] = client
		return client

	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "site")
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	resolver = QPMResolver.from_environment(
		directory_client_factory=client_factory,
		sleeper=lambda seconds: None,
	)

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		timeout=1,
	)

	assert resolved.service_id == "site-a-qpm"
	assert resolved.directory_scope == "site"
	assert resolved.directory_identity == "site-a"
	assert set(clients.keys()) == {"site-a"}


def test_resolver_from_environment_site_scope_reuses_bound_site_dirsvc(
		monkeypatch):
	bound_site = DirectoryClient([directory_record("site-qpm")])
	clients = {}

	def client_factory(endpoint):
		clients[endpoint] = DirectoryClient([])
		return clients[endpoint]

	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "site")
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", "yes")
	monkeypatch.setenv("QFW_DIRECT_QPM_ENDPOINT", "qpm-direct:9000")
	monkeypatch.delenv("QFW_LOCAL_DIRSVC_ENDPOINT", raising=False)
	resolver = QPMResolver.from_environment(
		dirsvc=bound_site,
		directory_client_factory=client_factory,
		sleeper=lambda seconds: None,
	)

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		timeout=1,
	)

	assert resolved.service_id == "site-qpm"
	assert resolved.directory_scope == "site"
	assert bound_site.queries
	assert clients == {}


def test_resolver_from_environment_keeps_order_with_local_and_site(
		monkeypatch):
	for order, expected in (
			("site,local", "site-qpm"),
			("local,site", "local-qpm")):
		local = DirectoryClient([directory_record("local-qpm")])
		site = DirectoryClient([directory_record("site-qpm")])
		clients = {}

		def client_factory(endpoint):
			clients[endpoint] = site
			return site

		monkeypatch.setenv("QFW_LOCAL_DIRSVC_ENDPOINT", "local-a")
		monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
		monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", order)
		monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
		resolver = QPMResolver.from_environment(
			dirsvc=local,
			directory_client_factory=client_factory,
			sleeper=lambda seconds: None,
		)

		resolved = resolver.resolve(
			service_type="qfw.qpm",
			selector_resource="IQM-20q",
			api_category="execution",
			timeout=1,
		)

		assert resolved.service_id == expected
		assert local.queries
		assert site.queries
		assert set(clients.keys()) == {"site-a"}


def test_resolver_from_environment_local_scope_does_not_query_site_or_direct(
		monkeypatch):
	local = DirectoryClient([])
	clients = {}

	def client_factory(endpoint):
		clients[endpoint] = DirectoryClient([directory_record("site-qpm")])
		return clients[endpoint]

	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
	monkeypatch.setenv("QFW_LOCAL_DIRSVC_ENDPOINT", "local-a")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "local")
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", "yes")
	monkeypatch.setenv("QFW_DIRECT_QPM_ENDPOINT", "qpm-direct:9000")
	resolver = QPMResolver.from_environment(
		dirsvc=local,
		directory_client_factory=client_factory,
		sleeper=lambda seconds: None,
	)

	try:
		resolver.resolve(
			service_type="qfw.qpm",
			selector_resource="IQM-20q",
			api_category="execution",
			timeout=1,
		)
	except DEFwReserveError:
		pass
	else:
		raise AssertionError("expected local-only resolver to reject fallback")

	assert local.queries
	assert clients == {}


def test_resolver_from_environment_allows_direct_endpoint(monkeypatch):
	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", "yes")
	monkeypatch.setenv("QFW_DIRECT_QPM_ENDPOINT", "qpm-direct:9000")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "direct")
	resolver = QPMResolver.from_environment(sleeper=lambda seconds: None)

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		api_category="execution",
		timeout=1,
	)

	assert resolved.service_id == "qpm-direct:9000"
	assert resolved.directory_scope == "direct"
	assert resolved.endpoint == "qpm-direct:9000"
	assert resolved.api_binding.client_class == "QPMExecution"


def test_resolver_from_environment_binds_site_directory_without_factory(
		monkeypatch):
	class FakeDefw:
		def __init__(self):
			self.binding_connections = []

		def connect_to_binding(self, resolved_binding):
			self.binding_connections.append(resolved_binding)
			binding = resolved_binding["selected_binding"]["binding_name"]
			if binding == "directory":
				record = directory_record("site-a-qpm")
				record["service_record"]["endpoint"] = "site-a-qpm:9020"
				return DirectoryClient([record])
			return "qpm-proxy"

	fake_defw = FakeDefw()
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a:8090")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "site")
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", raising=False)
	resolver = QPMResolver.from_environment(
		defw_module=fake_defw,
		sleeper=lambda seconds: None,
	)

	proxy = resolver.connect(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		timeout=1,
	)

	assert proxy == "qpm-proxy"
	assert [
		item["selected_binding"]["binding_name"]
		for item in fake_defw.binding_connections
	] == [
		"directory",
		"execution",
	]
	directory_endpoint = (
		fake_defw.binding_connections[0]["service_record"]["endpoint"])
	qpm_endpoint = fake_defw.binding_connections[1][
		"service_record"]["endpoint"]
	assert directory_endpoint["address"] == "site-a"
	assert directory_endpoint["listen_port"] == 8090
	assert qpm_endpoint["address"] == "site-a-qpm"
	assert qpm_endpoint["listen_port"] == 9020


def test_site_directory_client_accepts_resolve_services_only():
	class ResolveServicesOnly:
		def resolve_services(self, **kwargs):
			self.kwargs = kwargs
			return [directory_record("site-services-qpm")]

	class FakeDefw:
		def connect_to_binding(self, resolved_binding):
			self.resolved_binding = resolved_binding
			return ResolveServicesOnly()

	fake_defw = FakeDefw()
	client = DEFwDirectoryClient("site-a:8090", defw_module=fake_defw)

	records = client.resolve_service(
		service_name="QPM",
		service_type="qfw.qpm",
		binding_name="execution")

	assert len(records) == 1
	assert records[0]["service_record"]["service_id"] == "site-services-qpm"
	assert fake_defw.resolved_binding["selected_binding"]["binding_name"] == (
		"directory")


def test_direct_endpoint_connect_uses_defw_binding(monkeypatch):
	class FakeDefw:
		def __init__(self):
			self.binding_connections = []

		def connect_to_binding(self, resolved_binding):
			self.binding_connections.append(resolved_binding)
			return "qpm-proxy"

	fake_defw = FakeDefw()
	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", "yes")
	monkeypatch.setenv("QFW_DIRECT_QPM_ENDPOINT", "qpm-direct:9000")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "direct")
	monkeypatch.setenv("QFW_QPM_IMPL", "nwqsim")
	resolver = QPMResolver.from_environment(
		defw_module=fake_defw,
		sleeper=lambda seconds: None,
	)

	proxy = resolver.connect(service_type="qfw.qpm", timeout=1)

	assert proxy == "qpm-proxy"
	assert len(fake_defw.binding_connections) == 1
	record = fake_defw.binding_connections[0]
	assert record["selected_binding"]["binding_name"] == "execution"
	assert record["selected_binding"]["service_module"] == (
		"svc_nwqsim_qpm.svc_qpm")
	assert record["service_record"]["endpoint"]["address"] == "qpm-direct"
	assert record["service_record"]["endpoint"]["listen_port"] == 9000


def test_direct_endpoint_connect_reports_unsupported_without_binding(
		monkeypatch):
	class FakeDefw:
		pass

	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_ENABLED", "yes")
	monkeypatch.setenv("QFW_DIRECT_QPM_ENDPOINT", "qpm-direct:9000")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "direct")
	resolver = QPMResolver.from_environment(
		defw_module=FakeDefw(),
		sleeper=lambda seconds: None,
	)

	try:
		resolver.connect(service_type="qfw.qpm", timeout=1)
	except QPMUnsupportedConfigurationError as exc:
		assert "connect_to_binding" in str(exc)
	else:
		raise AssertionError("expected unsupported direct endpoint binding")


def test_category_routing_maps_to_binding_without_token_policy():
	assert binding_name_for_category("execution") == "execution"
	assert binding_name_for_category("telemetry") == "telemetry"
	assert binding_name_for_category("scheduler-control") == "scheduler"
	assert binding_name_for_category("execution", "custom") == "custom"


def test_resolver_preserves_binding_policy_labels_as_metadata():
	record = directory_record("qpm-policy-labels")
	record["selected_api_binding"]["policy_labels"] = [
		"reservation-required",
		"operator-only",
	]
	directory = DirectoryClient([record])
	resolver = QPMResolver(
		[DirectoryScope("site", "site", client=directory, priority=50)],
		connector=Connector(),
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		timeout=1,
	)

	assert resolved.api_binding.policy_labels == (
		"reservation-required",
		"operator-only",
	)


def test_resolver_accepts_defw_selected_binding_records():
	record = directory_record("site-a-qpm")
	record["service_record"]["endpoint"] = {
		"address": "10.0.0.5",
		"listen_port": 9020,
		"pid": 123,
		"node_name": "node-a",
		"hostname": "node-a.example",
	}
	record["selected_binding"] = record.pop("selected_api_binding")
	record["selected_binding"]["client_class"] = "QPMExecution"
	directory = DirectoryClient([record])

	class BindingDefw:
		def connect_to_binding(self, resolved_binding):
			self.resolved_binding = resolved_binding
			return "qpm-proxy"

	fake_defw = BindingDefw()
	resolver = QPMResolver(
		[DirectoryScope("site", "site", client=directory, priority=50)],
		connector=DEFwQPMConnector(fake_defw),
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	proxy = resolver.connect(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		timeout=1,
	)

	assert proxy == "qpm-proxy"
	assert fake_defw.resolved_binding["service_record"]["service_id"] == (
		"site-a-qpm")
	assert fake_defw.resolved_binding["selected_binding"]["client_class"] == (
		"QPMExecution")


def test_resolver_rejects_directory_record_without_selected_binding():
	record = directory_record("invalid-qpm")
	record.pop("selected_api_binding")
	resolver = QPMResolver(
		[DirectoryScope(
			"site", "site", client=DirectoryClient([record]), priority=50)],
		connector=Connector(),
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	try:
		resolver.resolve(
			service_type="qfw.qpm",
			selector_resource="IQM-20q",
			api_category="execution",
			timeout=1,
		)
	except QPMInvalidDirectoryRecordError as exc:
		assert "selected API binding" in str(exc)
	else:
		raise AssertionError("expected invalid directory record rejection")


def test_resolver_rejects_legacy_service_info_records():
	class LegacyServiceInfo:
		pass

	resolver = QPMResolver(
		[DirectoryScope(
			"site", "site",
			client=DirectoryClient([LegacyServiceInfo()]),
			priority=50)],
		connector=Connector(),
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	try:
		resolver.resolve(service_type="qfw.qpm", timeout=1)
	except QPMInvalidDirectoryRecordError as exc:
		assert "legacy DEFwServiceInfo" in str(exc)
	else:
		raise AssertionError("expected legacy service-info rejection")


def test_hardware_request_requires_explicit_simulator_fallback_policy():
	record = directory_record("local-sim-qpm")
	record["service_record"]["properties"] = {"simulator": True}
	resolver = QPMResolver(
		[DirectoryScope(
			"local", "allocation-local",
			client=DirectoryClient([record]), priority=100)],
		connector=Connector(),
		selection_order=["allocation-local"],
		sleeper=lambda seconds: None,
	)

	try:
		resolver.resolve(
			service_type="qfw.qpm",
			selector_resource="IQM-20q",
			api_category="execution",
			qpm_type=1,
			timeout=1,
		)
	except QPMSimulatorFallbackPolicyError as exc:
		assert "explicit simulator fallback policy required" in str(exc)
	else:
		raise AssertionError("expected simulator fallback policy rejection")

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		qpm_type=1,
		allow_simulator_fallback=True,
		timeout=1,
	)

	assert resolved.service_id == "local-sim-qpm"


def test_operation_modes_expose_same_qpm_api_after_binding():
	qpm_api = FakeQPMApi()
	fake_defw = BindingDefw(qpm_api)
	modes = [
		(
			"allocation-local",
			DirectoryScope(
				"local",
				"allocation-local",
				client=DirectoryClient([
					directory_record("local-qpm")
				]),
				priority=100,
			),
		),
		(
			"site",
			DirectoryScope(
				"site",
				"site",
				client=DirectoryClient([
					directory_record("site-qpm")
				]),
				priority=50,
			),
		),
	]
	results = []

	for scope, directory in modes:
		record = directory.client.records[0]
		record["service_record"]["endpoint"] = f"{scope}-qpm:9020"
		resolver = QPMResolver(
			[directory],
			connector=DEFwQPMConnector(fake_defw),
			selection_order=[scope],
			sleeper=lambda seconds: None,
		)
		proxy = resolver.connect(
			service_type="qfw.qpm",
			selector_resource="IQM-20q",
			api_category="execution",
			timeout=1,
		)

		assert proxy is qpm_api
		results.append((
			proxy.reserve(request={"device": "IQM-20q", "shots": 10}),
			proxy.release(reservation_id="reservation-1"),
			proxy.sync_run(
				{"qasm": "OPENQASM 2.0;", "num_shots": 10},
				reservation_id="reservation-1",
			),
			proxy.async_run(
				{"qasm": "OPENQASM 2.0;", "num_shots": 10},
				reservation_id="reservation-1",
			),
		))

	assert results[0] == results[1]
	assert [
		item["service_record"]["service_id"]
		for item in fake_defw.binding_connections
	] == ["local-qpm", "site-qpm"]
