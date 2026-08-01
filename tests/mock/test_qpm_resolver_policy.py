from qfw_qiskit.qpm_resolver import (
	DirectoryScope,
	QPMAmbiguousResolutionError,
	QPMResolver,
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

	def resolve_service(self, **kwargs):
		self.kwargs = kwargs
		return list(self.records)

	def get_service_generation(self, service_id):
		return self.latest_generation


class Connector:
	def __init__(self):
		self.connected = []

	def connect(self, resolved):
		self.connected.append(resolved)
		return resolved


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
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", raising=False)
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


def test_resolver_from_environment_allows_direct_endpoint_fallback(monkeypatch):
	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", "yes")
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
	assert resolved.api_binding.client_class == "QPM"


def test_resolver_from_environment_binds_site_directory_without_factory(
		monkeypatch):
	class FakeDefw:
		def __init__(self):
			self.endpoint_connections = []

		def connect_to_endpoint(self, endpoint, api_binding):
			self.endpoint_connections.append(
				(endpoint, api_binding.binding_name))
			if api_binding.binding_name == "directory":
				return DirectoryClient([directory_record("site-a-qpm")])
			return "qpm-proxy"

	fake_defw = FakeDefw()
	monkeypatch.setenv("QFW_SITE_DIRSVC_ENDPOINTS", "site-a")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "site")
	monkeypatch.delenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", raising=False)
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
	assert fake_defw.endpoint_connections == [
		("site-a", "directory"),
		("site-a-qpm", "execution"),
	]


def test_direct_endpoint_connect_reports_unsupported_without_binding(
		monkeypatch):
	class FakeDefw:
		pass

	monkeypatch.delenv("QFW_SITE_DIRSVC_ENDPOINTS", raising=False)
	monkeypatch.setenv("QFW_QPM_DIRECT_ENDPOINT_FALLBACK", "yes")
	monkeypatch.setenv("QFW_DIRECT_QPM_ENDPOINT", "qpm-direct:9000")
	monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "direct")
	resolver = QPMResolver.from_environment(
		defw_module=FakeDefw(),
		sleeper=lambda seconds: None,
	)

	try:
		resolver.connect(service_type="qfw.qpm", timeout=1)
	except QPMUnsupportedConfigurationError as exc:
		assert "connect_to_endpoint" in str(exc)
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
