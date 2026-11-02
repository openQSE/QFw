# Pasqal session policy and native submission, without provider packages.

import json
import os
import sys
import types

import pytest

from defw_exception import DEFwExecutionError
from svc_lib_qpm.drivers import qdmi_driver
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver
from svc_lib_qpm.drivers.qdmi_profiles import PasqalQdmiProfile, profile_for
from tests.mock.fakes import stub_qdmi_session


def _descriptor(**overrides):
	descriptor = {
		"provider": "pasqal", "id": "pasqal-emu-free",
		"provider_device_id": "EMU_FREE", "url": "https://apis.pasqal.cloud"}
	descriptor.update(overrides)
	return descriptor


@pytest.mark.parametrize("backend, technology", [
	("EMU_FREE", "neutral-atom"), ("emu_mps", "neutral-atom"), ("FRESNEL", "neutral-atom")])
def test_pasqal_selects_the_profile_and_technology(backend, technology):
	profile = profile_for(_descriptor(provider="PASQAL", provider_device_id=backend))
	assert isinstance(profile, PasqalQdmiProfile)
	assert profile.technology() == technology
	assert profile.calibration_set_slot is None


@pytest.fixture(autouse=True)
def _clear_defaults(monkeypatch):
	for name in ("QFW_API_KEY", "QFW_QC_URL", "PASQAL_EMAIL", "PASQAL_PASSWORD"):
		monkeypatch.delenv(name, raising=False)


def test_bound_token_and_project_override_service_defaults(monkeypatch):
	monkeypatch.setenv("QFW_API_KEY", "service-token")
	monkeypatch.setenv("QFW_QC_URL", "https://service.example")
	access = profile_for(_descriptor(project_id="device-project")).access({
		"api_key": "user-token", "project_id": "user-project", "url": "https://reserved.example"})
	assert access == {
		"qdmi_device_id": "pasqal.default", "base_url": "https://reserved.example",
		"token": "user-token", "custom1": "EMU_FREE", "custom3": "user-project"}


def test_service_defaults_and_descriptor_project(monkeypatch):
	monkeypatch.setenv("QFW_API_KEY", "service-token")
	access = profile_for(_descriptor(project_id="device-project")).access()
	assert access["token"] == "service-token"
	assert access["custom3"] == "device-project"
	assert access["base_url"] == "https://apis.pasqal.cloud"


def test_access_requires_a_backend_type():
	with pytest.raises(DEFwExecutionError, match="provider-device-id"):
		profile_for(_descriptor(provider_device_id=None)).access()


def test_session_uses_packaged_catalogue_and_reservation_cache(monkeypatch):
	opened = []

	def open_device(device_id, **kwargs):
		opened.append((device_id, kwargs))
		return object()

	provider = types.ModuleType("pasqal.qdmi")
	provider.PASQAL_QDMI_DEVICE_ID = "pasqal.default"
	core = types.ModuleType("mqt.core.qdmi")
	core.builtin_driver = types.SimpleNamespace(open_device=open_device)
	monkeypatch.setitem(sys.modules, provider.__name__, provider)
	monkeypatch.setitem(sys.modules, core.__name__, core)
	driver = QdmiDriver(_descriptor(qdmi_device_id="pasqal.emu-free"))
	first = driver._device({"reservation_id": "first"})
	assert driver._device({"reservation_id": "first"}) is first
	assert driver._device({"reservation_id": "second"}) is not first
	assert driver.evict_reservation("first")
	assert driver._device({"reservation_id": "first"}) is not first
	assert opened == [("pasqal.emu-free", {
		"base_url": "https://apis.pasqal.cloud", "custom1": "EMU_FREE",
		"token": None, "custom3": None})] * 3


def test_missing_library_names_the_required_package(monkeypatch):
	monkeypatch.setitem(sys.modules, "pasqal.qdmi", None)
	with pytest.raises(DEFwExecutionError, match="pasqal-qdmi"):
		QdmiDriver(_descriptor())._device()


@pytest.mark.parametrize("shots", [0, -1, 11])
def test_shot_limits_are_checked_before_submission(shots):
	with pytest.raises(DEFwExecutionError):
		profile_for(_descriptor(max_shots=10)).check_shots(shots)


@pytest.mark.parametrize("info", [
	{"qasm": "OPENQASM 2.0;"},
	{"circuit": {"format": "qpy", "data": "UVBZ"}}])
def test_gate_circuits_are_rejected_before_loading_qiskit(info):
	with pytest.raises(DEFwExecutionError, match="Pulser sequences only"):
		profile_for(_descriptor()).circuit_source(info)


def test_native_submission_preserves_atom_counts_and_job_metadata(monkeypatch):
	counts = {"001": 7, "100": 3}
	job = types.SimpleNamespace(
		id="pasqal-batch", queue_position=2, check=lambda: "DONE", get_counts=lambda: counts)
	submitted = []

	def submit_job(*args, **kwargs):
		submitted.append((args, kwargs))
		return job

	driver = QdmiDriver(_descriptor())
	stub_qdmi_session(driver, types.SimpleNamespace(submit_job=submit_job))
	monkeypatch.setattr(driver._profile, "encode", lambda driver, source, *args: (source, "CUSTOM1", None))
	module = types.ModuleType("mqt.core.qdmi")
	module.ProgramFormat = types.SimpleNamespace(CUSTOM1="fmt-pulser")
	monkeypatch.setitem(sys.modules, module.__name__, module)
	records = []

	def to_result_record(result, shots, provider, device_id, **kwargs):
		records.append((result, shots, provider, device_id, kwargs))
		return {"schema": "qhw-result-v1"}

	monkeypatch.setattr(qdmi_driver.qdmi_normalize, "to_result_record", to_result_record)
	circuit = types.SimpleNamespace(
		info={"circuit": {"format": "pulser-abstract-repr", "data": "native-program"},
			"num_shots": 10, "poll_interval": 0.0}, get_cid=lambda: "cid-pasqal")
	assert driver.run_circuit(circuit) == {"schema": "qhw-result-v1"}
	assert submitted == [(("native-program", "fmt-pulser", 10), {})]
	assert records == [({"100": 7, "001": 3}, 10, "pasqal", "pasqal-emu-free", {
		"job_id": "pasqal-batch", "status": "completed", "queue_position": 2})]
	assert driver.get_task_metadata("cid-pasqal")["job_id"] == "pasqal-batch"


def test_token_session_disables_service_login_fallback(monkeypatch):
	opened = []
	provider = types.ModuleType("pasqal.qdmi")
	provider.PASQAL_QDMI_DEVICE_ID = "pasqal.default"
	core = types.ModuleType("mqt.core.qdmi")
	core.builtin_driver = types.SimpleNamespace(open_device=lambda *args, **kwargs: opened.append(kwargs))
	monkeypatch.setitem(sys.modules, provider.__name__, provider)
	monkeypatch.setitem(sys.modules, core.__name__, core)
	driver = QdmiDriver(_descriptor())
	driver._device({"token": "user-token", "project_id": "project-a"})
	driver._device({"token": "user-token", "project_id": "project-b"})
	assert len(opened) == 2
	assert [settings["custom3"] for settings in opened] == ["project-a", "project-b"]
	assert all(settings["auth_file"] == os.devnull for settings in opened)


def test_backend_info_uses_the_shared_pulser_record(monkeypatch):
	core = types.ModuleType("mqt.core.qdmi")
	core.CustomProperty = types.SimpleNamespace(CUSTOM1="specs-slot")
	monkeypatch.setitem(sys.modules, core.__name__, core)
	specs = [{"device_type": "FRESNEL", "specs": "device-json"}]
	lookups = []
	device = types.SimpleNamespace(name=lambda: "EMU_FREE", query_custom_property=lambda slot, kind: (
		lookups.append((slot, kind)) or json.dumps(specs)))
	driver = QdmiDriver(_descriptor())
	driver._device = lambda credential: lookups.append(credential) or device
	assert driver.get_backend_info({"reservation_id": 7}) == {
		"backend": "pasqal", "metadata_supported": True, "device_type": "EMU_FREE",
		"pulser_devices": {"FRESNEL": "device-json"}}
	assert lookups == [{"reservation_id": 7}, ("specs-slot", str)]


@pytest.mark.parametrize("resource_type", ["PasqalLocal", "UnknownResource"])
def test_noncloud_resources_fail_before_opening(resource_type):
	with pytest.raises(DEFwExecutionError, match="use PasqalCloud"):
		profile_for(_descriptor(resource_type=resource_type)).access()


def test_pasqal_example_selects_each_library_through_the_existing_frontend(monkeypatch):
	from pathlib import Path
	import yaml
	from svc_lib_qpm import descriptor
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	from svc_lib_qpm.frontend import Frontend
	from util import device_access
	path = Path(__file__).resolve().parents[2] / "examples/qfw_pulser_device_access.yaml"
	config = yaml.safe_load(path.read_text())
	monkeypatch.setattr(device_access, "device_access_config_path", lambda: str(path))
	monkeypatch.setattr(device_access, "load_yaml_config", lambda path: config)
	selected = descriptor.resolve_descriptor("pasqal-emu-free")
	qdmi = QdmiDriver(selected)
	qrmi = QrmiDriver(selected)
	frontend = Frontend([qdmi, qrmi], selected)
	assert frontend.route("run_circuit", lib="qdmi") is qdmi
	assert frontend.route("run_circuit", lib="qrmi") is qrmi
	assert frontend.route("run_circuit") is qrmi
	assert frontend.route("get_backend_info", lib="qdmi") is qdmi
	assert frontend.route("get_backend_info", lib="qrmi") is qrmi


@pytest.mark.parametrize("variable", ["PASQAL_EMAIL", "PASQAL_PASSWORD"])
def test_token_sessions_reject_ambient_account_credentials(monkeypatch, variable):
	monkeypatch.setenv(variable, "service-default")
	with pytest.raises(DEFwExecutionError, match=variable):
		QdmiDriver(_descriptor())._device({"token": "user-token"})
