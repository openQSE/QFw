# Guards Pasqal devices on the shim's QRMI path.
#
# QRMI's PasqalCloud resource takes a Pulser sequence and a shot count as
# Payload.PasqalCloud, and returns the batch's {"counter": {...}}. The
# resource id is the Pasqal device, so it is the provider-device-id. Its
# target() lists Pulser devices, which no qhw normalizer reads, so the
# resource serves execution and get_backend_info, which hands those devices to
# a Pulser client. PasqalLocal needs the Slurm job's identity and a session
# acquired for it, which a shim service does not hold.
#
# The Pasqal Cloud project a job is billed to comes from the user's credential
# DB entry, and otherwise from the device's project-id.
#
# qrmi and qhw_data are stood in for, so no install is needed.

import json
import pathlib
import sys
import types

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402
from svc_lib_qpm.frontend import Frontend  # noqa: E402
from util import device_access  # noqa: E402


SEQUENCE = '{"version": "1", "name": "pulser-exported"}'


def _descriptor(**overrides):
	descriptor = {
		"provider": "pasqal",
		"id": "pasqal-emu-free",
		"provider_device_id": "EMU_FREE",
		"resource_type": "PasqalCloud",
		"project_id": "proj-device",
	}
	descriptor.update(overrides)
	return descriptor


def _circuit(info=None):
	info = info or {
		"circuit": {"format": "pulser-abstract-repr", "data": SEQUENCE},
		"num_qubits": 4, "num_shots": 50, "poll_interval": 0.0}
	return types.SimpleNamespace(info=info, get_cid=lambda: "cid-1")


class _Resource:
	# A PasqalCloud resource whose batch is done at the first poll.
	def __init__(self):
		self.started = []
		self.stopped = []

	def task_start(self, payload):
		self.started.append(payload)
		return "batch-1"

	def task_status(self, job_id):
		return "Completed"

	def task_result(self, job_id):
		return types.SimpleNamespace(
			value=json.dumps({"counter": {"0000": 45, "1000": 5}}))

	def task_stop(self, job_id):
		self.stopped.append(job_id)


class _Record(dict):
	# Stands in for qhw_data's result builder.
	def job(self, **kwargs):
		self["job"] = kwargs
		return self

	def result(self, **kwargs):
		self["result"] = kwargs
		return self

	def metadata(self, value):
		self["metadata"] = value
		return self

	def build(self):
		return dict(self)


def _qrmi_driver(monkeypatch, resource, **descriptor):
	monkeypatch.setitem(sys.modules, "qhw_data", types.SimpleNamespace(
		new_result=lambda provider, device_id, job_status: _Record(
			provider=provider, device=device_id, job_status=job_status)))
	driver = QrmiDriver(_descriptor(**descriptor))
	driver._resource = lambda: types.SimpleNamespace(
		ResourceType=types.SimpleNamespace(
			PasqalCloud="type-cloud", PasqalLocal="type-local"),
		Payload=types.SimpleNamespace(
			PasqalCloud=lambda **kwargs: ("PasqalCloud", kwargs)))
	driver._qpu = lambda credential=None: resource
	return driver


# --- execution ---------------------------------------------------------------

def test_the_sequence_runs_as_a_pasqal_cloud_payload(monkeypatch):
	resource = _Resource()

	record = _qrmi_driver(monkeypatch, resource).run_circuit(_circuit())

	assert resource.started == [
		("PasqalCloud", {"sequence": SEQUENCE, "job_runs": 50})]
	assert record == {
		"provider": "pasqal",
		"device": "pasqal-emu-free",
		"job_status": "completed",
		"job": {"id": "batch-1", "status": "completed"},
		"result": {
			"shots": 50, "num_circuits": 1,
			"counts": {"0000": 45, "1000": 5}, "success": True},
		"metadata": {"source": "qrmi", "via": "qrmi.PasqalCloud"},
	}


def test_a_finished_batch_is_not_cancelled(monkeypatch):
	# task_stop cancels a Pasqal batch, and Pasqal rejects cancelling one
	# that has ended, so the cleanup other providers get is skipped.
	resource = _Resource()

	_qrmi_driver(monkeypatch, resource).run_circuit(_circuit())

	assert resource.stopped == []


@pytest.mark.parametrize("result", [{"error": "unexpected"}, {"counter": {}}])
def test_a_result_without_counts_is_an_error(monkeypatch, result):
	# A completed job with no counts is not an empty success: a Pulser
	# client cannot build a result from it.
	resource = _Resource()
	resource.task_result = lambda job_id: types.SimpleNamespace(
		value=json.dumps(result))

	with pytest.raises(DEFwExecutionError, match="returned no counts"):
		_qrmi_driver(monkeypatch, resource).run_circuit(_circuit())


def test_the_provider_deadline_is_the_device_job_timeout(monkeypatch):
	# The device's job-timeout-seconds, else none: a cloud QPU's queue can
	# outlast any default, and the reservation closing cancels the job. The
	# IBM override does not reach other providers.
	monkeypatch.setenv("QFW_IBM_JOB_TIMEOUT_SECONDS", "60")
	waits = []
	for descriptor in ({"job_timeout_seconds": 1800}, {}):
		driver = _qrmi_driver(monkeypatch, _Resource(), **descriptor)
		driver._poll_task = lambda job_id, timeout, poll, **kwargs: (
			waits.append(timeout) or "completed")
		driver.run_circuit(_circuit())

	assert waits == [1800, None]


def test_a_timeout_that_is_not_a_number_fails_before_submitting(
		monkeypatch):
	# Read only once the job ran, it would leave the job at the provider.
	resource = _Resource()
	driver = _qrmi_driver(monkeypatch, resource, job_timeout_seconds="30m")

	with pytest.raises(DEFwExecutionError, match="number of seconds"):
		driver.run_circuit(_circuit())
	assert resource.started == []


@pytest.mark.parametrize("provider", ["iqm", "ibm"])
def test_other_providers_wait_the_device_job_timeout_else_300s(provider):
	waits = []
	for descriptor in ({"job_timeout_seconds": 900}, {}):
		driver = QrmiDriver(dict(descriptor, provider=provider))
		driver._qpu = lambda credential=None: _Resource()
		driver._poll_task = lambda job_id, timeout, poll, **kwargs: (
			waits.append(timeout) or "completed")
		driver._run_sampler_payload("payload", _circuit())

	assert waits == [900.0, 300.0]


def test_a_job_without_a_project_names_where_to_set_it(monkeypatch):
	monkeypatch.delenv("PASQAL_CONFIG_ROOT", raising=False)
	resource = _Resource()
	driver = _qrmi_driver(monkeypatch, resource, project_id=None)

	with pytest.raises(DEFwExecutionError) as excinfo:
		driver.run_circuit(_circuit())

	assert "EMU_FREE_QRMI_PASQAL_CLOUD_PROJECT_ID" in str(excinfo.value)
	assert "project-id" in str(excinfo.value)
	assert resource.started == []


def test_a_config_root_may_name_the_project(monkeypatch):
	# QRMI reads project_id from the file, which the shim never opens.
	monkeypatch.setenv("PASQAL_CONFIG_ROOT", "/etc/qfw/pasqal")
	resource = _Resource()

	_qrmi_driver(monkeypatch, resource, project_id=None).run_circuit(
		_circuit())

	assert len(resource.started) == 1


def test_pasqal_local_is_refused_before_anything_starts(monkeypatch):
	resource = _Resource()
	driver = _qrmi_driver(monkeypatch, resource, resource_type="PasqalLocal")

	with pytest.raises(DEFwExecutionError, match="PasqalLocal is not supported"):
		driver.run_circuit(_circuit())

	assert resource.started == []


def test_a_circuit_is_refused_by_name(monkeypatch):
	resource = _Resource()
	driver = _qrmi_driver(monkeypatch, resource)

	with pytest.raises(
			DEFwExecutionError,
			match="Pulser sequences only and received a openqasm2 circuit"):
		driver.run_circuit(_circuit({"qasm": "OPENQASM 2.0;"}))

	assert resource.started == []


# --- what a Pasqal resource serves -------------------------------------------

def test_a_pasqal_resource_serves_execution_and_its_pulser_devices():
	descriptor = _descriptor(libraries=["qrmi"])
	frontend = Frontend([QrmiDriver(descriptor)], descriptor | {
		"caps": {call: ["qrmi"] for call in (
			"get_device_info", "get_backend_info", "run_circuit",
			"get_task_timing", "get_task_metadata")}})

	assert frontend.capability_map()["get_device_info"] == []
	assert frontend.capability_map()["get_backend_info"] == ["qrmi"]
	assert frontend.capability_map()["run_circuit"] == ["qrmi"]
	assert QrmiDriver({"provider": "iqm"}).implements("get_device_info")


def test_backend_info_is_the_pulser_devices_target_lists():
	# An emulator's target() lists the real devices' public specs, a QPU's
	# only its own. They are read for every call, so a recalibration shows.
	reads = []

	def target():
		reads.append(1)
		return types.SimpleNamespace(value=json.dumps([
			{"device_type": "FRESNEL_CAN1", "specs": "{can1}"},
			{"device_type": "FRESNEL", "specs": "{fresnel}"}]))

	driver = QrmiDriver(_descriptor())
	driver._qpu = lambda credential=None: types.SimpleNamespace(target=target)

	driver.get_backend_info()
	assert len(reads) == 1
	assert driver.get_backend_info() == {
		"backend": "pasqal",
		"metadata_supported": True,
		"device_type": "EMU_FREE",
		"pulser_devices": {"FRESNEL_CAN1": "{can1}", "FRESNEL": "{fresnel}"},
	}
	assert len(reads) == 2


# --- the project a job is billed to ------------------------------------------

def _database(project_id="proj-alice"):
	device = {"enabled": True, "api_key": "alice-token"}
	if project_id:
		device["project_id"] = project_id
	return {"users": {"alice": {
		"enabled": True, "devices": {"pasqal-emu-free": device}}}}


def test_user_entry_names_its_project():
	_user, record = device_access.select_user_record(
		_database(), "alice", device_id="pasqal-emu-free")

	assert device_access.get_project_id_from_user_record(
		record, "pasqal-emu-free") == "proj-alice"
	_user, record = device_access.select_user_record(
		_database(project_id=None), "alice", device_id="pasqal-emu-free")
	assert device_access.get_project_id_from_user_record(
		record, "pasqal-emu-free") is None


def test_the_users_project_reaches_the_qrmi_resource(tmp_path):
	# From the credential DB, through the bound secret, to the config map
	# QRMI opens the resource with, over the device's own project.
	import util.qpm.credentials as qpm_credentials
	database = tmp_path / "qpu-users.json"
	database.write_text(json.dumps(_database()), encoding="utf-8")
	device = {
		"device_id": "pasqal-emu-free",
		"provider_device_id": "EMU_FREE",
		"provider": "pasqal",
		"url": "https://apis.pasqal.cloud",
		"credential_db": str(database),
	}
	provider = qpm_credentials.FileCredentialProvider(
		str(tmp_path / "device-access.yaml"), device)

	secret = provider.bind({"user": "alice"}).secret
	assert secret["project_id"] == "proj-alice"

	config = QrmiDriver(_descriptor())._resource_config(
		"PasqalCloud", "EMU_FREE", credential=secret)
	assert config["QRMI_PASQAL_CLOUD_PROJECT_ID"] == "proj-alice"
	assert config["QRMI_PASQAL_CLOUD_AUTH_TOKEN"] == "alice-token"
	assert config["QRMI_PASQAL_CLOUD_BASE_URL"] == "https://apis.pasqal.cloud"


def test_resolved_device_access_carries_the_users_project(
		tmp_path, monkeypatch):
	(tmp_path / "qpu-users.json").write_text(
		json.dumps(_database()), encoding="utf-8")
	config = tmp_path / "device-access.yaml"
	config.write_text(
		"qpus:\n"
		"  pasqal-emu-free:\n"
		"    provider: pasqal\n"
		"    provider-device-id: EMU_FREE\n"
		"    url: https://apis.pasqal.cloud\n"
		"    credential-db: qpu-users.json\n",
		encoding="utf-8")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	access = device_access.resolve_device_access(
		device_id="pasqal-emu-free", user="alice")

	assert access["api_key"] == "alice-token"
	assert access["project_id"] == "proj-alice"


def test_credentials_differing_by_project_open_separate_resources():
	driver = QrmiDriver(_descriptor())
	alice = {"user": "alice", "token": "tok", "project_id": "proj-a"}

	assert driver._credential_cache_key(alice) != \
		driver._credential_cache_key(dict(alice, project_id="proj-b"))


def test_an_entitlement_provider_carries_the_users_project(tmp_path):
	# A site whose service account authenticates still bills each user to
	# the project their entry names.
	import util.qpm.credentials as qpm_credentials
	database = tmp_path / "qpu-users.json"
	database.write_text(json.dumps(_database()), encoding="utf-8")
	device = {
		"device_id": "pasqal-emu-free",
		"provider_device_id": "EMU_FREE",
		"provider": "pasqal",
		"url": "https://apis.pasqal.cloud",
		"credential_db": str(database),
		"credential_provider_type": "entitlement",
	}
	provider = qpm_credentials.EntitlementCredentialProvider(
		str(tmp_path / "device-access.yaml"), device)

	secret = provider.bind({"user": "alice"}).secret
	assert secret["project_id"] == "proj-alice"
	assert "api_key" not in secret
	assert device_access.resolve_qpu_credentials(
		device, user="alice")["project_id"] == "proj-alice"
