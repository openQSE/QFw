# MQV session policy and submission, without provider packages.

import sys
import types

import pytest

from defw_exception import DEFwExecutionError
from svc_lib_qpm.drivers import qdmi_driver
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver
from svc_lib_qpm.drivers.qdmi_profiles import MqvQdmiProfile, profile_for
from tests.mock.fakes import stub_qdmi_session


def _descriptor(**overrides):
	descriptor = {
		"provider": "mqv", "id": "mqv-lrz",
		"qdmi_device_id": "mqv.lrz", "url": "https://mqss.example/api/"}
	descriptor.update(overrides)
	return descriptor


@pytest.fixture(autouse=True)
def _clear_defaults(monkeypatch):
	for name in ("QFW_API_KEY", "QFW_QC_URL"):
		monkeypatch.delenv(name, raising=False)


def _stub_core(monkeypatch, registered=("mqv.lrz",)):
	"""Stand in for MQT Core's driver and Qiskit plugin; returns the opens."""
	opened = []

	def open_device(device_id, **settings):
		opened.append((device_id, settings))
		return types.SimpleNamespace(name=lambda: device_id)

	qdmi = types.ModuleType("mqt.core.qdmi")
	qdmi.builtin_driver = types.SimpleNamespace(
		registered_device_ids=lambda: list(registered), open_device=open_device)
	plugin = types.ModuleType("mqt.core.plugins.qiskit")
	plugin.QDMIBackend = lambda device, device_id=None: types.SimpleNamespace(
		device=device, device_id=device_id)
	monkeypatch.setitem(sys.modules, qdmi.__name__, qdmi)
	monkeypatch.setitem(sys.modules, plugin.__name__, plugin)
	return opened


@pytest.mark.parametrize("provider", ["mqv", "MQV"])
def test_mqv_selects_its_profile(provider):
	profile = profile_for(_descriptor(provider=provider))
	assert isinstance(profile, MqvQdmiProfile)
	assert profile.calibration_set_slot is None


def test_the_reservations_token_and_url_come_first(monkeypatch):
	monkeypatch.setenv("QFW_API_KEY", "service-token")
	monkeypatch.setenv("QFW_QC_URL", "https://service.example")
	access = profile_for(_descriptor()).access(
		{"api_key": "user-token", "url": "https://reserved.example/"})
	assert access == {
		"qdmi_device_id": "mqv.lrz", "base_url": "https://reserved.example",
		"token": "user-token"}


def test_the_device_url_is_the_default_endpoint(monkeypatch):
	monkeypatch.setenv("QFW_API_KEY", "service-token")
	access = profile_for(_descriptor()).access()
	assert access["base_url"] == "https://mqss.example/api"
	assert access["token"] == "service-token"


def test_the_endpoint_may_come_from_the_catalogue_entry(monkeypatch):
	monkeypatch.setenv("QFW_API_KEY", "service-token")
	assert profile_for(_descriptor(url=None)).access()["base_url"] is None


def test_access_requires_a_qdmi_device_id(monkeypatch):
	monkeypatch.setenv("QFW_API_KEY", "service-token")
	with pytest.raises(DEFwExecutionError, match="qdmi-device-id"):
		profile_for(_descriptor(qdmi_device_id=None)).access()


def test_access_without_a_token_looks_up_device_access(monkeypatch):
	from util import device_access

	def resolve_device_access(**kwargs):
		raise RuntimeError("no credential for this user")

	monkeypatch.setattr(device_access, "resolve_device_access", resolve_device_access)
	with pytest.raises(DEFwExecutionError, match="MQV device access"):
		profile_for(_descriptor()).access({"user": "alice"})


def test_a_session_opens_the_named_device_once_per_reservation(monkeypatch):
	opened = _stub_core(monkeypatch)
	driver = QdmiDriver(_descriptor())
	first = driver._device({"reservation_id": "first", "api_key": "token-a"})
	assert driver._device({"reservation_id": "first", "api_key": "token-a"}) is first
	assert driver._device({"reservation_id": "second", "api_key": "token-b"}) is not first
	assert opened == [
		("mqv.lrz", {"base_url": "https://mqss.example/api", "token": "token-a"}),
		("mqv.lrz", {"base_url": "https://mqss.example/api", "token": "token-b"})]


def test_settings_left_unset_are_not_passed_to_the_library(monkeypatch):
	opened = _stub_core(monkeypatch)
	QdmiDriver(_descriptor(url=None))._device({"api_key": "token-a"})
	assert opened == [("mqv.lrz", {"token": "token-a"})]


def test_an_unregistered_device_names_the_package(monkeypatch):
	opened = _stub_core(monkeypatch, registered=("mqt.ddsim.default",))
	with pytest.raises(DEFwExecutionError, match="MQV's QDMI device package"):
		QdmiDriver(_descriptor())._device({"api_key": "token-a"})
	assert opened == []


def test_a_device_without_openqasm_is_refused_before_compiling():
	device = types.SimpleNamespace(
		name=lambda: "MQSS", supported_program_formats=lambda: [
			types.SimpleNamespace(name="QIR_BASE_STRING")])
	with pytest.raises(DEFwExecutionError, match="QASM2, QASM3.*QIR_BASE_STRING"):
		profile_for(_descriptor()).encode(
			None, "OPENQASM 2.0;", {}, types.SimpleNamespace(device=device))


@pytest.mark.parametrize("shots", [0, -1, 11])
def test_shot_limits_are_checked_before_submission(shots):
	with pytest.raises(DEFwExecutionError):
		profile_for(_descriptor(max_shots=10)).check_shots(shots)


def test_submission_returns_the_devices_counts_and_job_metadata(monkeypatch):
	counts = {"000": 6, "111": 4}
	job = types.SimpleNamespace(
		id="mqss-job", queue_position=1, check=lambda: "DONE",
		get_counts=lambda: counts)
	submitted = []

	def submit_job(*args, **kwargs):
		submitted.append((args, kwargs))
		return job

	driver = QdmiDriver(_descriptor())
	stub_qdmi_session(driver, types.SimpleNamespace(submit_job=submit_job))
	monkeypatch.setattr(
		driver._profile, "encode",
		lambda driver, source, *args: ("OPENQASM 2.0; ...", "QASM2", None))
	module = types.ModuleType("mqt.core.qdmi")
	module.ProgramFormat = types.SimpleNamespace(QASM2="fmt-qasm2")
	monkeypatch.setitem(sys.modules, module.__name__, module)
	records = []

	def to_result_record(result, shots, provider, device_id, **kwargs):
		records.append((result, shots, provider, device_id, kwargs))
		return {"schema": "qhw-result-v1"}

	monkeypatch.setattr(qdmi_driver.qdmi_normalize, "to_result_record", to_result_record)
	circuit = types.SimpleNamespace(
		info={"qasm": "OPENQASM 2.0;", "num_shots": 10, "poll_interval": 0.0},
		get_cid=lambda: "cid-mqv")
	assert driver.run_circuit(circuit) == {"schema": "qhw-result-v1"}
	assert submitted == [(("OPENQASM 2.0; ...", "fmt-qasm2", 10), {})]
	assert records == [(counts, 10, "mqv", "mqv-lrz", {
		"job_id": "mqss-job", "status": "completed", "queue_position": 1})]
	assert driver.get_task_metadata("cid-mqv")["job_id"] == "mqss-job"
