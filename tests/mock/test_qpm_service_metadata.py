def _register_directory_qpm(directory, service_id, type_bits, caps_bits, *,
			    provider="iqm", resource="IQM-20q",
			    runtime_id=None, peer_handle=None):
	directory.register_service({
		"service_id": service_id,
		"service_name": "QPM",
		"service_type": "qfw.qpm",
		"runtime_id": runtime_id or f"{service_id}-runtime",
		"peer_handle": peer_handle or f"{service_id}-peer",
		"endpoint": {
			"address": f"{service_id}.example",
			"listen_port": 9020,
			"pid": 123,
			"node_name": service_id,
			"hostname": f"{service_id}.example",
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
			"resources": [resource],
			"aliases": [provider],
		},
		"properties": {
			"provider": provider,
		},
		"capability": {
			"type": int(type_bits),
			"caps": int(caps_bits),
		},
		"qpm_type": int(type_bits),
		"qpm_capabilities": int(caps_bits),
	})


def test_qpm_metadata_resolves_through_defw_directory_shape():
	import defw_directory
	from api_qpm_common import QPMCapability, QPMType
	from qfw_qiskit.qpm_resolver import DirectoryScope, QPMResolver
	from util.qpm.util_qpm import UTIL_QPM

	class MetadataQPM(UTIL_QPM):
		def controller_telemetry(self):
			return {"target_id": "iqm-target"}

	qpm = MetadataQPM.__new__(MetadataQPM)
	info = qpm.query_helper(
		QPMType.QPM_TYPE_HARDWARE,
		QPMCapability.QPM_CAP_SUPERCONDUCTING,
		"QPM",
		"Quantum Platform Manager",
		properties={
			"provider": "iqm",
			"num_qubits": 20,
		},
	)
	properties = info["properties"]
	directory = defw_directory.Directory()
	directory.register_service({
		"service_id": info["service_id"],
		"service_name": info["service_name"],
		"service_type": info["service_type"],
		"runtime_id": "runtime-1",
		"peer_handle": "peer-1",
		"endpoint": {
			"address": "qpm.example",
			"listen_port": 9020,
			"pid": 123,
			"node_name": "qpm-node",
			"hostname": "qpm.example",
		},
		"api_bindings": info["api_bindings"],
		"selector": info["selector"],
		"properties": properties,
		"capability": info["capability"],
		"qpm_type": info["qpm_type"],
		"qpm_capabilities": info["qpm_capabilities"],
	})
	resolver = QPMResolver(
		[DirectoryScope("site-a", "site", client=directory, priority=50)],
		connector=None,
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		selector_resource="IQM-20q",
		api_category="execution",
		timeout=1,
	)

	assert resolved.service_id == properties["service_id"]
	assert resolved.service_type == "qfw.qpm"
	assert resolved.properties["provider"] == "iqm"
	assert resolved.properties["qpm_type"] == int(QPMType.QPM_TYPE_HARDWARE)
	assert resolved.properties["qpm_capabilities"] == int(
		QPMCapability.QPM_CAP_SUPERCONDUCTING)
	assert resolved.selector_metadata["resources"] == ["IQM-20q"]
	assert resolved.api_binding.binding_name == "execution"
	assert resolved.api_binding.client_module == "api_qpm_execution"
	assert resolved.api_binding.client_class == "QPMExecution"


def test_resolver_filters_real_directory_records_by_type_and_capability():
	import defw_directory
	from api_qpm_common import QPMCapability, QPMType
	from qfw_qiskit.qpm_resolver import DirectoryScope, QPMResolver

	directory = defw_directory.Directory()
	_register_directory_qpm(
		directory,
		"qpm-nwqsim",
		QPMType.QPM_TYPE_SIMULATOR,
		QPMCapability.QPM_CAP_STATEVECTOR,
		provider="nwqsim",
		resource="shared-target",
	)
	_register_directory_qpm(
		directory,
		"qpm-iqm",
		QPMType.QPM_TYPE_HARDWARE,
		QPMCapability.QPM_CAP_SUPERCONDUCTING,
		provider="iqm",
		resource="shared-target",
	)
	resolver = QPMResolver(
		[DirectoryScope("site-a", "site", client=directory, priority=50)],
		connector=None,
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		selector_resource="shared-target",
		api_category="execution",
		qpm_type=QPMType.QPM_TYPE_HARDWARE,
		qpm_capabilities=QPMCapability.QPM_CAP_SUPERCONDUCTING,
		timeout=1,
	)

	assert resolved.service_id == "qpm-iqm"


def test_resolver_filters_real_directory_records_by_provider_metadata():
	import defw_directory
	from api_qpm_common import QPMCapability, QPMType
	from qfw_qiskit.qpm_resolver import DirectoryScope, QPMResolver

	directory = defw_directory.Directory()
	_register_directory_qpm(
		directory,
		"qpm-nwqsim",
		QPMType.QPM_TYPE_SIMULATOR,
		QPMCapability.QPM_CAP_STATEVECTOR,
		provider="nwqsim",
		resource="statevector-target",
	)
	_register_directory_qpm(
		directory,
		"qpm-qb",
		QPMType.QPM_TYPE_SIMULATOR,
		QPMCapability.QPM_CAP_STATEVECTOR,
		provider="qb",
		resource="statevector-target",
	)
	resolver = QPMResolver(
		[DirectoryScope("site-a", "site", client=directory, priority=50)],
		connector=None,
		selection_order=["site"],
		sleeper=lambda seconds: None,
	)

	resolved = resolver.resolve(
		service_type="qfw.qpm",
		selector_resource="statevector-target",
		api_category="execution",
		qpm_type=QPMType.QPM_TYPE_SIMULATOR,
		qpm_capabilities=QPMCapability.QPM_CAP_STATEVECTOR,
		provider="nwqsim",
		timeout=1,
	)

	assert resolved.service_id == "qpm-nwqsim"
	assert resolved.properties["provider"] == "nwqsim"


def test_resolver_rejects_real_directory_stale_generation_before_binding():
	import defw_directory
	from api_qpm_common import QPMCapability, QPMType
	from qfw_qiskit.qpm_resolver import (
		DirectoryScope,
		QPMResolver,
		QPMStaleGenerationError,
	)

	class RestartingDirectory(defw_directory.Directory):
		def __init__(self):
			super().__init__()
			self.restarted = False

		def resolve_services(self, **filters):
			records = super().resolve_services(**filters)
			if not self.restarted:
				self.restarted = True
				self.deregister_service("qpm-iqm", "runtime-1", 1)
				_register_directory_qpm(
					self,
					"qpm-iqm",
					QPMType.QPM_TYPE_HARDWARE,
					QPMCapability.QPM_CAP_SUPERCONDUCTING,
					provider="iqm",
					resource="IQM-20q",
					runtime_id="runtime-2",
					peer_handle="peer-2",
				)
			return records

	class Connector:
		def __init__(self):
			self.connected = []

		def connect(self, resolved):
			self.connected.append(resolved)
			return resolved

	directory = RestartingDirectory()
	_register_directory_qpm(
		directory,
		"qpm-iqm",
		QPMType.QPM_TYPE_HARDWARE,
		QPMCapability.QPM_CAP_SUPERCONDUCTING,
		provider="iqm",
		resource="IQM-20q",
		runtime_id="runtime-1",
		peer_handle="peer-1",
	)
	connector = Connector()
	resolver = QPMResolver(
		[DirectoryScope("site-a", "site", client=directory, priority=50)],
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
