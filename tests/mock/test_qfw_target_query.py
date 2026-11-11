import json
import sys
import types

import pytest

import qfw_qiskit.qfw_target_query as target_query
import qfw_qiskit.qpm_resolver as qpm_resolver

RESOLVED = types.SimpleNamespace(
	service_id="aer-0123abcd", runtime_id="runtime-1",
	properties={"provider": "aer", "num_qubits": 30, "aer_mode": "ideal",
		    "aer_device": "GPU", "aer_method": "matrix_product_state"})
NO_DEVICE = {"backend": "aer-0123abcd", "metadata_supported": False}


class FakeTelemetryAPI:
	def __init__(self, failures=()):
		self.failures = set(failures)
		self.calls = []

	def __getattr__(self, name):
		def call(**kwargs):
			self.calls.append(name)
			if name in self.failures:
				raise RuntimeError(f"{name} unavailable")
			if name == "get_backend_info":
				return NO_DEVICE
			return {"record": name}
		return call


class FakeResolver:
	def __init__(self):
		self.requests = []
		self.api = FakeTelemetryAPI()
		self.bound = []

	def resolve(self, **request):
		self.requests.append(request)
		return RESOLVED

	def managed_binding(self, resolved):
		def api(binding_name, expected_runtime_id=None):
			self.bound.append((binding_name, expected_runtime_id))
			return self.api
		return types.SimpleNamespace(api=api)

	def __getattr__(self, name):
		raise AssertionError(f"qfw-target must not call {name}")


@pytest.fixture
def resolver(monkeypatch):
	fake = FakeResolver()
	monkeypatch.setattr(
		qpm_resolver.QPMResolver, "from_environment",
		classmethod(lambda cls, **kwargs: fake))
	monkeypatch.setattr(
		sys.modules["defw_app_util"], "defw_get_directory_service",
		lambda timeout=None: object())
	return fake


def test_the_document_carries_the_service_settings_and_records():
	document = target_query.target_document(RESOLVED, FakeTelemetryAPI())

	assert document["schema"] == "qfw-target-v1"
	assert document["service_id"] == "aer-0123abcd"
	assert document["provider"] == "aer"
	assert document["settings"]["aer_device"] == "GPU"
	assert document["backend_info"] == NO_DEVICE
	assert document["device_info"] == {"record": "get_device_info"}
	assert document["coupling_graph"] == {"record": "get_coupling_graph"}
	assert document["calibration"] == {"record": "get_calibration_snapshot"}
	assert "errors" not in document


def test_a_record_the_qpm_cannot_answer_is_null_with_its_error():
	document = target_query.target_document(
		RESOLVED, FakeTelemetryAPI(failures={"get_coupling_graph"}))

	assert document["coupling_graph"] is None
	assert document["errors"] == {
		"coupling_graph": "get_coupling_graph unavailable"}


def test_the_query_resolves_and_reads_telemetry_without_reserving(resolver):
	document = target_query.query(provider="aer", service_id="aer-0123abcd")

	assert resolver.requests == [{
		"timeout": 10, "binding_name": "execution", "provider": "aer",
		"service_id": "aer-0123abcd"}]
	assert resolver.bound == [("telemetry", "runtime-1")]
	assert document["service_id"] == "aer-0123abcd"


def test_the_command_writes_the_document(resolver, tmp_path):
	output = tmp_path / "target.json"

	assert target_query.main(["--provider", "aer", "--output", str(output)]) == 0

	assert json.loads(output.read_text())["settings"]["aer_mode"] == "ideal"


def test_a_device_less_document_gets_the_generic_target_at_the_qpm_width():
	document = target_query.target_document(RESOLVED, FakeTelemetryAPI())

	target = target_query.target_from_json(json.dumps(document))

	assert target.num_qubits == 30


def test_a_document_of_another_schema_is_refused():
	with pytest.raises(ValueError, match="qfw-target-v1"):
		target_query.target_from_json({"schema": "qfw-target-v0"})
