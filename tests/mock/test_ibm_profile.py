# IBM session settings and policy, without SDK imports or provider calls.

import sys
import types

import pytest

from defw_exception import DEFwExecutionError
from svc_lib_qpm.drivers import qdmi_driver
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver
from svc_lib_qpm.drivers.qdmi_profiles import IbmQdmiProfile, profile_for
from tests.mock.fakes import stub_qdmi_session
from util import device_access


def _descriptor(**overrides):
	descriptor = {
		"provider": "ibm", "id": "ibm-torino",
		"provider_device_id": "ibm_torino"}
	descriptor.update(overrides)
	return descriptor


def _credential(**overrides):
	credential = {
		"url": "https://reserved.example/api/v1/", "api_key": "reserved-key",
		"provider_device_id": "reserved-backend", "service_crn": "crn:reserved"}
	credential.update(overrides)
	return credential


@pytest.fixture(autouse=True)
def _clear_access_defaults(monkeypatch):
	for name in ("QFW_QC_URL", "QFW_API_KEY", "QFW_IBM_SERVICE_CRN", "QFW_IBM_IAM_ENDPOINT"):
		monkeypatch.delenv(name, raising=False)


def _install_backend(monkeypatch, opened):
	def IBMBackend(**kwargs):
		opened.append(kwargs)
		return types.SimpleNamespace(device=object())
	module = types.ModuleType("ibm.qdmi.qiskit")
	module.IBMBackend = IBMBackend
	monkeypatch.setitem(sys.modules, module.__name__, module)


def test_ibm_selects_the_profile_and_superconducting_technology():
	profile = profile_for(_descriptor(provider="IBM"))
	assert isinstance(profile, IbmQdmiProfile)
	assert profile.technology() == "superconducting"
	assert profile.calibration_set_slot is None


def test_bound_access_wins_over_service_defaults(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://service.example/api/v1")
	monkeypatch.setenv("QFW_API_KEY", "service-key")
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:service")

	def resolve(**kwargs):
		raise AssertionError("a complete reservation must not resolve service credentials")
	monkeypatch.setattr(device_access, "resolve_device_access", resolve)
	access = profile_for(_descriptor(service_crn="crn:descriptor")).access(_credential())
	assert access == {
		"qdmi_device_id": "ibm.default", "base_url": "https://reserved.example/api",
		"api_key": "reserved-key", "backend_name": "reserved-backend",
		"instance_crn": "crn:reserved", "auth_url": None}


def test_service_defaults_precede_database_credentials(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://service.example/api/v1")
	monkeypatch.setenv("QFW_API_KEY", "service-key")
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:service")
	monkeypatch.setattr(device_access, "resolve_device_access", lambda **kwargs: {
		"url": "https://database.example", "api_key": "database-key",
		"provider_device_id": "database-backend", "service_crn": "crn:database"})
	access = profile_for(_descriptor(service_crn="crn:descriptor")).access()
	assert access["base_url"] == "https://service.example/api"
	assert access["api_key"] == "service-key"
	assert access["backend_name"] == "ibm_torino"
	assert access["instance_crn"] == "crn:service"


def test_database_access_keeps_the_selected_user_and_instance(monkeypatch):
	seen = []

	def resolve(**kwargs):
		seen.append(kwargs)
		return {
			"url": "https://database.example/api/v1", "api_key": "database-key",
			"provider_device_id": "database-backend", "service_crn": "crn:database"}
	monkeypatch.setattr(device_access, "resolve_device_access", resolve)
	access = profile_for(_descriptor(service_crn="crn:descriptor")).access({
		"user": "alice", "credential_hint": "hint", "credential_handle": "handle"})
	assert seen == [{
		"provider": "ibm", "device_id": "ibm-torino", "user": "alice",
		"credential_hint": "hint", "credential_handle": "handle"}]
	assert access["api_key"] == "database-key"
	assert access["instance_crn"] == "crn:database"


def test_database_instance_fills_in_environment_credentials(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://service.example/api/v1")
	monkeypatch.setenv("QFW_API_KEY", "service-key")
	monkeypatch.setattr(device_access, "resolve_device_access", lambda **kwargs: {
		"url": "https://database.example", "api_key": "database-key",
		"service_crn": "crn:database"})
	access = profile_for(_descriptor()).access({"user": "alice"})
	assert access["base_url"] == "https://service.example/api"
	assert access["api_key"] == "service-key"
	assert access["instance_crn"] == "crn:database"


def test_database_backend_fills_in_bound_credentials(monkeypatch):
	monkeypatch.setattr(device_access, "resolve_device_access", lambda **kwargs: {
		"provider_device_id": "database-backend"})
	access = profile_for(_descriptor(provider_device_id=None)).access(
		_credential(provider_device_id=None))
	assert access["backend_name"] == "database-backend"
	assert access["api_key"] == "reserved-key"
	assert access["instance_crn"] == "crn:reserved"


def test_descriptor_instance_is_used_when_credentials_do_not_supply_one(monkeypatch):
	def resolve(**kwargs):
		raise AssertionError("complete session settings must not resolve credentials again")
	monkeypatch.setattr(device_access, "resolve_device_access", resolve)
	access = profile_for(_descriptor(service_crn="crn:descriptor")).access(
		_credential(service_crn=None))
	assert access["instance_crn"] == "crn:descriptor"


@pytest.mark.parametrize("field, message", [
	("url", "URL"), ("api_key", "API"),
	("provider_device_id", "provider-device-id"), ("service_crn", "service-crn")])
def test_missing_session_settings_fail_before_opening(monkeypatch, field, message):
	monkeypatch.setattr(device_access, "resolve_device_access", lambda **kwargs: {})
	credential = _credential()
	del credential[field]
	profile = profile_for(_descriptor(provider_device_id=None))
	with pytest.raises(DEFwExecutionError, match=message):
		profile.access(credential)


@pytest.mark.parametrize("url, expected", [
	("https://quantum.example/api/v1/", "https://quantum.example/api"),
	("https://quantum.example/api/", "https://quantum.example/api"),
	("https://quantum.example/v11", "https://quantum.example/v11")])
def test_base_url_normalization_keeps_other_path_suffixes(url, expected):
	access = profile_for(_descriptor()).access(_credential(url=url))
	assert access["base_url"] == expected


@pytest.mark.parametrize("endpoint", [
	"https://iam.example/", "https://iam.example/identity/token"])
def test_iam_endpoint_uses_the_token_path_once(endpoint):
	access = profile_for(_descriptor(iam_endpoint=endpoint)).access(_credential())
	assert access["auth_url"] == "https://iam.example/identity/token"


def test_iam_environment_overrides_the_descriptor(monkeypatch):
	monkeypatch.setenv("QFW_IBM_IAM_ENDPOINT", "https://service-iam.example/")
	access = profile_for(_descriptor(iam_endpoint="https://device-iam.example")).access(_credential())
	assert access["auth_url"] == "https://service-iam.example/identity/token"


def test_session_opens_with_the_packaged_backend(monkeypatch):
	opened = []
	_install_backend(monkeypatch, opened)
	driver = QdmiDriver(_descriptor(qdmi_device_id="ibm.custom"))
	device = driver._device(_credential())
	assert device is driver._device(_credential())
	assert opened == [{
		"device_id": "ibm.custom", "base_url": "https://reserved.example/api",
		"api_key": "reserved-key", "backend_name": "reserved-backend",
		"instance_crn": "crn:reserved", "auth_url": None}]
	assert driver._profile.describe(driver._access(_credential())) == "reserved-backend"


def test_missing_library_names_the_required_package(monkeypatch):
	monkeypatch.setitem(sys.modules, "ibm.qdmi.qiskit", None)
	with pytest.raises(DEFwExecutionError, match="ibm-qdmi"):
		QdmiDriver(_descriptor())._device(_credential())


def test_distinct_instances_cannot_share_a_nonreservation_backend(monkeypatch):
	opened = []
	_install_backend(monkeypatch, opened)
	driver = QdmiDriver(_descriptor())
	first = driver._device(_credential(service_crn="crn:first"))
	second = driver._device(_credential(service_crn="crn:second"))
	assert first is not second
	assert driver._device(_credential(service_crn="crn:first")) is first
	assert [access["instance_crn"] for access in opened] == ["crn:first", "crn:second"]


@pytest.mark.parametrize("shots", [0, -1, 11])
def test_shot_limits_are_checked_before_submission(shots):
	with pytest.raises(DEFwExecutionError):
		profile_for(_descriptor(max_shots=10)).check_shots(shots)


def test_native_submission_preserves_classical_counts(monkeypatch):
	counts = {"10010": 7, "00101": 3}
	states = iter(["RUNNING", "DONE"])
	job = types.SimpleNamespace(
		id="ibm-job", queue_position=None,
		check=lambda: next(states), get_counts=lambda: counts)
	submitted = []

	def submit_job(*args, **kwargs):
		submitted.append((args, kwargs))
		return job
	driver = QdmiDriver(_descriptor())
	stub_qdmi_session(driver, types.SimpleNamespace(submit_job=submit_job))
	monkeypatch.setattr(driver._profile, "encode", lambda *args: ("program", "QASM3", None))
	module = types.ModuleType("mqt.core.qdmi")
	module.ProgramFormat = types.SimpleNamespace(QASM3="fmt-qasm3")
	monkeypatch.setitem(sys.modules, module.__name__, module)
	records = []

	def to_result_record(result, shots, provider, device_id, **kwargs):
		records.append((result, shots, provider, device_id, kwargs))
		return {"schema": "qhw-result-v1"}
	monkeypatch.setattr(qdmi_driver.fomac_normalize, "to_result_record", to_result_record)
	circuit = types.SimpleNamespace(
		info={"qasm": "source", "num_shots": 10, "poll_interval": 0.0},
		get_cid=lambda: "cid-ibm")
	assert driver.run_circuit(circuit) == {"schema": "qhw-result-v1"}
	assert submitted == [(("program", "fmt-qasm3", 10), {})]
	assert records == [(counts, 10, "ibm", "ibm-torino", {
		"job_id": "ibm-job", "status": "completed", "queue_position": None})]
