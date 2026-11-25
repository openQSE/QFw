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
		self.reserved = []
		self.api = FakeTelemetryAPI()
		self.bound = []
		self.resolved = RESOLVED

	def resolve(self, **request):
		self.requests.append(request)
		return self.resolved

	def managed_binding(self, resolved):
		def api(binding_name, expected_runtime_id=None):
			self.bound.append((binding_name, expected_runtime_id))
			return self.api
		return types.SimpleNamespace(api=api)

	def connect_reserved_managed(self, service_id, reservation_id, **kwargs):
		self.reserved.append((service_id, reservation_id))
		return types.SimpleNamespace(
			resolved=self.resolved, reservation_id=reservation_id,
			lifecycle_binding=self.managed_binding(self.resolved))

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
	monkeypatch.delenv("QFW_RESERVATIONS", raising=False)
	monkeypatch.delenv("QFW_QPM_IMPL", raising=False)
	monkeypatch.delenv("QFW_SHIM_LIB", raising=False)
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

	[request] = resolver.requests
	assert (request["provider"], request["service_id"], request["timeout"]) == (
		"aer", "aer-0123abcd", 10)
	assert resolver.reserved == []
	assert resolver.bound == [("telemetry", "runtime-1")]
	assert document["service_id"] == "aer-0123abcd"


def test_the_command_writes_the_document(resolver, tmp_path):
	output = tmp_path / "target.json"

	assert target_query.main(["--provider", "aer", "--output", str(output)]) == 0

	assert json.loads(output.read_text())["settings"]["aer_mode"] == "ideal"


def test_a_job_with_a_reservation_describes_the_reserved_qpm(resolver, monkeypatch):
	monkeypatch.setenv("QFW_RESERVATIONS", '[["fake-iqm-0123", "7"]]')

	document = target_query.query()

	assert [service_id for service_id, _id in resolver.reserved] == ["fake-iqm-0123"]
	assert resolver.requests == []
	assert document["service_id"] == "aer-0123abcd"


def test_without_a_provider_the_job_s_qpm_impl_picks_the_qpm(
		resolver, monkeypatch):
	monkeypatch.setenv("QFW_QPM_IMPL", "fake-iqm")

	target_query.query()

	assert resolver.requests[0]["provider"] == "fake-iqm"


def test_a_shim_qpm_is_described_through_the_job_s_shim_library(
		resolver, monkeypatch):
	monkeypatch.setenv("QFW_SHIM_LIB", "qdmi")
	resolver.resolved = types.SimpleNamespace(
		service_id="shim-a", runtime_id="runtime-1",
		properties={"provider": "shim"})
	calls = []
	resolver.api = types.SimpleNamespace(**{
		call: (lambda call: lambda **kwargs: calls.append((call, kwargs)))(call)
		for _key, call in target_query.RECORDS})

	target_query.query(provider="shim")

	assert {kwargs.get("lib") for _call, kwargs in calls} == {"qdmi"}


def test_a_device_less_document_gets_the_backends_generic_target():
	from qfw_qiskit.qfw_target import QFW_NUM_QUBITS

	document = target_query.target_document(RESOLVED, FakeTelemetryAPI())

	target = target_query.target_from_json(json.dumps(document))

	assert target.num_qubits == QFW_NUM_QUBITS


def test_a_record_error_is_its_message_without_the_remote_stack():
	class RemoteError(Exception):
		msg = "no credential record for user qfw"

		def __str__(self):
			return "DEFwError:\n  msg: ...\n  stack: ...\n"

	document = target_query.target_document(
		RESOLVED, types.SimpleNamespace(**{
			call: (lambda **kwargs: (_ for _ in ()).throw(RemoteError()))
			for _key, call in target_query.RECORDS}))

	assert set(document["errors"].values()) == {
		"no credential record for user qfw"}


def test_a_failed_query_is_one_line(resolver, monkeypatch, capsys):
	def refuse(**request):
		raise RuntimeError("no QPM matches provider 'aer'\nin 2 directories")

	monkeypatch.setattr(resolver, "resolve", refuse)

	assert target_query.main(["--provider", "aer"]) == 1

	assert capsys.readouterr().err == (
		"qfw-target: no QPM matches provider 'aer' in 2 directories\n")


def test_a_document_of_another_schema_is_refused():
	with pytest.raises(ValueError, match="qfw-target-v1"):
		target_query.target_from_json({"schema": "qfw-target-v0"})
