import pytest
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
	assert resolved.properties["controller_target_id"] == "iqm-target"
	assert "controller" not in resolved.properties
	assert resolved.properties["api_bindings"] == info["api_bindings"]
	assert resolved.selector_metadata["resources"] == ["IQM-20q"]
	assert resolved.api_binding.binding_name == "execution"
	assert resolved.api_binding.client_module == "api_qpm_execution"
	assert resolved.api_binding.client_class == "QPMExecution"


def _query_without_runtime(qpm_class):
	qpm = qpm_class.__new__(qpm_class)
	qpm.controller_telemetry = lambda: {"state": "test"}
	return qpm.query()["properties"]


@pytest.mark.parametrize(
	("module_name", "provider", "device_id", "native"),
	(
		("svc_fake_iqm_qpm.svc_qpm", "fake-iqm", "fake-iqm-20q", False),
		("svc_iqm_qpm.svc_qpm", "iqm", "iqm-test", False),
		("svc_lib_qpm.svc_qpm", "shim", "fake-iqm-20q", False),
		("svc_nwqsim_qpm.svc_qpm", "nwqsim", "nwqsim", True),
		("svc_qb_qpm.svc_qpm", "qb", "qb", True),
		("svc_tnqvm_qpm.svc_qpm", "tnqvm", "tnqvm", True),
	),
)
def test_supported_qpm_queries_publish_exact_execution_contract(
		monkeypatch, module_name, provider, device_id, native):
	monkeypatch.setenv("QFW_QPU_DEVICE_ID", device_id)
	module = __import__(module_name, fromlist=["QPM"])
	properties = _query_without_runtime(module.QPM)

	assert properties["provider"] == provider
	assert properties["device_id"] == device_id
	assert isinstance(properties["num_qubits"], int)
	assert properties["program_inputs"] == [{
		"representation": "openqasm",
		"versions": ["2.0"],
		"preference": 0,
		"native": native,
	}]
	assert "credentials" not in properties
	assert "telemetry_methods" not in properties
	assert any(
		binding["binding_name"] == "execution"
		and binding["version"] == 1
		for binding in properties["api_bindings"]
	)


def test_nwqsim_publishes_authoritative_qhw_topology_and_operations():
	from svc_nwqsim_qpm.svc_qpm import QPM

	qpm = QPM.__new__(QPM)
	device = qpm.get_device_info()
	coupling = qpm.get_coupling_graph()

	assert device["schema"] == "qhw-device-v1"
	assert device["device"] == {
		"id": "nwqsim", "num_qubits": 32}
	assert len(device["qubits"]) == 32
	assert coupling["schema"] == "qhw-coupling-v1"
	assert coupling["coupling"]["directed"] is True
	assert len(coupling["coupling"]["edges"]) == 32 * 31
	assert [item["name"] for item in coupling["operations"]] == [
		"rz", "sx", "x", "cx", "measure", "reset"]
	assert coupling["operations"][0]["parameters"] == [{"name": "theta"}]
	assert qpm.get_calibration_snapshot() is None


@pytest.mark.parametrize(
	"module_name",
	("svc_iqm_qpm.svc_qpm", "svc_lib_qpm.svc_qpm"),
)
def test_unconfigured_physical_provider_is_explicitly_unqualified(
		monkeypatch, module_name):
	monkeypatch.delenv("QFW_QPU_DEVICE_ID", raising=False)
	module = __import__(module_name, fromlist=["QPM"])
	properties = _query_without_runtime(module.QPM)

	assert "device_id" not in properties
	assert properties["qualification_status"] == "unsupported"
	assert properties["unsupported_reason"] == "device-id-unconfigured"


@pytest.mark.parametrize(
	"reservation_service_ids",
	(
		("qpm:iqm:single",),
		("qpm:iqm:first", "qpm:iqm:second"),
	),
)
def test_query_identity_matches_each_reserved_service(
		monkeypatch, reservation_service_ids):
	from api_qpm_common import QPMCapability, QPMType
	from util.qpm.util_qpm import UTIL_QPM, openqasm_2_program_inputs

	class ReservedQPM(UTIL_QPM):
		PROGRAM_INPUTS = tuple(openqasm_2_program_inputs(native=False))

		def controller_telemetry(self):
			return {"state": "test"}

	qpm = ReservedQPM.__new__(ReservedQPM)
	observed = []
	for service_id in reservation_service_ids:
		monkeypatch.setenv("QFW_QPM_SERVICE_ID", service_id)
		info = qpm.query_helper(
			QPMType.QPM_TYPE_HARDWARE,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			"QPM",
			"Reserved QPM",
			properties={
				"provider": "iqm",
				"device_id": service_id.rsplit(":", 1)[-1],
				"num_qubits": 20,
			},
		)
		observed.append(info["properties"]["service_id"])

	assert tuple(observed) == reservation_service_ids


@pytest.mark.parametrize(
	"module_name",
	(
		"svc_fake_iqm_qpm.svc_qpm",
		"svc_iqm_qpm.svc_qpm",
		"svc_lib_qpm.svc_qpm",
		"svc_nwqsim_qpm.svc_qpm",
		"svc_qb_qpm.svc_qpm",
		"svc_tnqvm_qpm.svc_qpm",
	),
)
def test_each_advertised_input_has_an_exact_submission_path(module_name):
	from defw_exception import DEFwExecutionError

	module = __import__(module_name, fromlist=["QPM"])
	qpm = module.QPM.__new__(module.QPM)
	qpm.validate_program_input({
		"qasm": "OPENQASM 2.0; qreg q[1];",
		"program_representation": "openqasm",
		"program_version": "2.0",
	})
	with pytest.raises(DEFwExecutionError, match="no QPM execution path"):
		qpm.validate_program_input({
			"qasm": "OPENQASM 2.0; qreg q[1];",
			"program_representation": "openqasm",
			"program_version": "3.0",
		})
	with pytest.raises(DEFwExecutionError, match="only exact OpenQASM 2.0"):
		qpm.validate_program_input({"qasm": "OPENQASM 3.0; qubit q;"})


def test_query_rejects_advertisement_without_execution_path(monkeypatch):
	from api_qpm_common import QPMCapability, QPMType
	from util.qpm.util_qpm import UTIL_QPM

	class InvalidQPM(UTIL_QPM):
		PROGRAM_INPUTS = ({
			"representation": "qpy",
			"versions": ["13"],
			"preference": 0,
			"native": True,
		},)

		def controller_telemetry(self):
			return {"state": "test"}

	qpm = InvalidQPM.__new__(InvalidQPM)
	with pytest.raises(ValueError, match="no execution path"):
		qpm.query_helper(
			QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_STATEVECTOR,
			"QPM",
			"Invalid QPM",
			properties={
				"provider": "invalid",
				"device_id": "invalid",
				"num_qubits": 1,
			},
		)


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


def test_reserved_resolution_uses_exact_service_without_substitution():
	import defw_directory
	from api_qpm_common import QPMCapability, QPMType
	from qfw_qiskit.qpm_resolver import DirectoryScope, QPMResolver

	directory = defw_directory.Directory()
	_register_directory_qpm(
		directory, "qpm-a", QPMType.QPM_TYPE_SIMULATOR,
		QPMCapability.QPM_CAP_STATEVECTOR, provider="nwqsim")
	_register_directory_qpm(
		directory, "qpm-b", QPMType.QPM_TYPE_SIMULATOR,
		QPMCapability.QPM_CAP_STATEVECTOR, provider="nwqsim")

	class Connector:
		def connect(self, resolved):
			return f"client:{resolved.service_id}"

	resolver = QPMResolver([
		DirectoryScope("site-a", "site", client=directory, priority=50),
	], connector=Connector(), selection_order=["site"],
		sleeper=lambda seconds: None)
	binding = resolver.connect_reserved("qpm-b", 17, timeout=1)

	assert binding.resolved.service_id == "qpm-b"
	assert binding.client == "client:qpm-b"
	assert binding.reservation_id == 17


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
