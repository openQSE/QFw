# Guards the QDMI driver's provider profiles (drivers/qdmi_profiles.py).
#
# The driver keeps QDMI's session and job lifecycle and asks a per-provider
# profile for the vendor-defined parts: how a session opens and from which
# settings, which program format a job takes, what the CUSTOM slots mean,
# and how counts are keyed. The IQM profile is the driver's behavior from
# before profiles existed, so these tests pin that behavior down: the same
# registration, the same open_device call, the same IQM_JSON submission.
#
# The device libraries and mqt.core are stood in for through sys.modules, so
# no qiskit, iqm-qdmi or mqt-core install is needed.

import pathlib
import sys
import types

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers import fomac_normalize, qdmi_driver  # noqa: E402
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver  # noqa: E402
from svc_lib_qpm.drivers.qdmi_profiles import (  # noqa: E402
	IqmQdmiProfile, profile_for)


# --- choosing a profile ------------------------------------------------------

def test_the_default_profile_is_iqm():
	assert isinstance(profile_for({}), IqmQdmiProfile)
	assert isinstance(profile_for(None), IqmQdmiProfile)
	assert isinstance(profile_for({"provider": "IQM"}), IqmQdmiProfile)


def test_an_unknown_provider_fails_when_the_driver_is_built():
	with pytest.raises(
			DEFwExecutionError, match="no profile for provider 'ionq'"):
		QdmiDriver({"provider": "ionq"})


# --- the IQM profile: the session opens as it did before ---------------------

class _Definition:
	# Stands in for mqt.core.qdmi.driver.DeviceDefinition.
	def __init__(self, device_id, library_path, prefix):
		self.device_id = device_id
		self.library_path = library_path
		self.prefix = prefix


def _module(name, **attrs):
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	return module


def _install_mqt_driver(monkeypatch, opened):
	# mqt.core.qdmi.driver with a recording open_device. Returns the list the
	# registrations land in.
	registered = []

	def register_device_if_absent(definition):
		registered.append(definition)
		return True

	def open_device(device_id, **kwargs):
		opened.append((device_id, kwargs))
		return "device-handle"

	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module("mqt.core.qdmi"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi.driver", _module(
		"mqt.core.qdmi.driver",
		DeviceDefinition=_Definition,
		register_device_if_absent=register_device_if_absent,
		open_device=open_device))
	return registered


def _install_iqm_qdmi(monkeypatch):
	monkeypatch.setitem(sys.modules, "iqm", _module("iqm"))
	monkeypatch.setitem(sys.modules, "iqm.qdmi", _module(
		"iqm.qdmi",
		IQM_QDMI_DEVICE_ID="iqm.default",
		IQM_QDMI_LIBRARY_PATH="/opt/libiqm-qdmi.so",
		IQM_QDMI_PREFIX="IQM"))


def test_the_iqm_session_opens_as_it_did_before(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://qc.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "secret")
	opened = []
	registered = _install_mqt_driver(monkeypatch, opened)
	_install_iqm_qdmi(monkeypatch)
	driver = QdmiDriver({
		"provider": "iqm", "id": "ornl-iqm-20q",
		"provider_device_id": "default"})

	assert driver._device() == "device-handle"

	assert [(d.device_id, d.library_path, d.prefix) for d in registered] == [
		("iqm.default", "/opt/libiqm-qdmi.so", "IQM")]
	# The trailing slash is stripped, the token rides in TOKEN and the
	# quantum computer alias in CUSTOM2, as iqm.qdmi.qiskit does.
	assert opened == [("iqm.default", {
		"base_url": "https://qc.example.org",
		"token": "secret",
		"custom2": "default",
	})]
	# The session is opened once and cached.
	assert driver._device() == "device-handle"
	assert len(opened) == 1


def test_the_iqm_profile_names_the_missing_credential(monkeypatch):
	monkeypatch.delenv("QFW_QC_URL", raising=False)
	monkeypatch.delenv("QFW_API_KEY", raising=False)
	import util.device_access as device_access
	monkeypatch.setattr(
		device_access, "resolve_device_access",
		lambda **kwargs: {
			"url": "https://qc.example.org/", "api_key": None,
			"device_id": "ornl-iqm-20q", "provider_device_id": "default"})

	with pytest.raises(DEFwExecutionError, match="API token"):
		QdmiDriver({"provider": "iqm"})._access()


# --- the reservation's credential, not the service's ------------------------

def _bound_credential(user="shehataa", api_key="user-secret",
		reservation_id=7):
	# What FileCredentialProvider binds for a reservation and UTIL_QPM
	# attaches to the circuit as provider_credential.
	return {
		"url": "https://qc.example.org/",
		"api_key": api_key,
		"token": api_key,
		"device_id": "ornl-iqm-20q",
		"provider_device_id": "default",
		"quantum_computer": "default",
		"user": user,
		"reservation_id": reservation_id,
	}


def test_the_session_opens_with_the_reservation_credential(monkeypatch):
	# The service's own settings are there too, and must not win: the QPM
	# runs as one account for every user.
	monkeypatch.setenv("QFW_QC_URL", "https://service.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "service-secret")
	opened = []
	_install_mqt_driver(monkeypatch, opened)
	_install_iqm_qdmi(monkeypatch)
	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})

	driver._device(credential=_bound_credential())

	assert opened == [("iqm.default", {
		"base_url": "https://qc.example.org",
		"token": "user-secret",
		"custom2": "default",
	})]


def test_device_access_is_resolved_for_the_reservation_user(monkeypatch):
	# A credential with no token falls back to device-access config, and
	# that has to look up the reservation's user, not the account the
	# service runs as (resolve_qpu_user would answer root).
	monkeypatch.delenv("QFW_QC_URL", raising=False)
	monkeypatch.delenv("QFW_API_KEY", raising=False)
	import util.device_access as device_access
	calls = []

	def resolve_device_access(**kwargs):
		calls.append(kwargs)
		return {
			"url": "https://qc.example.org/", "api_key": "user-secret",
			"device_id": "ornl-iqm-20q", "provider_device_id": "default"}

	monkeypatch.setattr(
		device_access, "resolve_device_access", resolve_device_access)
	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})

	access = driver._access({"user": "shehataa", "credential_hint": "hint"})

	assert access["token"] == "user-secret"
	assert calls == [{
		"provider": "iqm",
		"device_id": "ornl-iqm-20q",
		"user": "shehataa",
		"credential_hint": "hint",
		"credential_handle": None,
	}]


def test_each_reservation_gets_its_own_session(monkeypatch):
	# A session carries the token it opened with, so one user's circuit
	# must never run on a session opened for another. Keyed by reservation,
	# as the native IQM QPM keys its clients, so it can be dropped when the
	# reservation ends.
	monkeypatch.setenv("QFW_QC_URL", "https://service.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "service-secret")
	opened = []
	_install_mqt_driver(monkeypatch, opened)
	_install_iqm_qdmi(monkeypatch)
	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})
	alice = _bound_credential("alice", "alice-secret", reservation_id=7)
	bob = _bound_credential("bob", "bob-secret", reservation_id=8)

	driver._device(credential=alice)
	driver._device(credential=bob)
	driver._device(credential=alice)
	# The same user's next reservation is a session of its own.
	driver._device(credential=dict(alice, reservation_id=9))
	driver._device()

	assert [kwargs["token"] for _, kwargs in opened] == [
		"alice-secret", "bob-secret", "alice-secret", "service-secret"]


def test_an_ended_reservation_loses_its_session(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://service.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "service-secret")
	opened = []
	_install_mqt_driver(monkeypatch, opened)
	_install_iqm_qdmi(monkeypatch)
	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})
	alice = _bound_credential("alice", "alice-secret", reservation_id=7)
	driver._device(credential=alice)
	driver._device()

	assert driver.evict_reservation(7) is True
	# Nothing left for it, and the service's own session is untouched.
	assert driver.evict_reservation(7) is False
	assert list(driver._devices) == [("default",)]
	# A circuit that still names the reservation opens a fresh session
	# rather than finding the old one.
	driver._device(credential=alice)
	assert len(opened) == 3


def test_a_missing_device_library_names_its_package(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://qc.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "secret")
	_install_mqt_driver(monkeypatch, [])
	# A None entry makes `import iqm` fail the way an absent package does.
	monkeypatch.setitem(sys.modules, "iqm", None)
	monkeypatch.setitem(sys.modules, "iqm.qdmi", None)

	with pytest.raises(DEFwExecutionError, match="Install iqm-qdmi"):
		QdmiDriver({"provider": "iqm"})._device()


def test_the_iqm_profile_encodes_an_iqm_json_program(monkeypatch):
	import util.iqm_transcode as iqm_transcode
	calls = []

	def build_iqm_circuit(source, dynamic, mapping):
		calls.append((source, dynamic, mapping))
		return "iqm-circuit"

	monkeypatch.setattr(iqm_transcode, "build_iqm_circuit", build_iqm_circuit)
	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "extract_topology",
		lambda device: {"qubits": ["QB1", "QB2"]})
	driver = QdmiDriver({"provider": "iqm"})
	driver._serialize_program = lambda iqm_circuit: f"json:{iqm_circuit}"

	encoded = driver._profile.encode(
		driver, "OPENQASM 2.0;", {"qubit_mapping": {"q0": "QB2"}}, object())

	# The format is a name, resolved by the driver only at submission.
	assert encoded == ("json:iqm-circuit", "IQM_JSON", None)
	assert calls == [
		("OPENQASM 2.0;", {"qubits": ["QB1", "QB2"]}, {"q0": "QB2"})]


def test_the_iqm_profile_defaults():
	profile = profile_for({"provider": "iqm"})

	assert profile.calibration_set_slot == "CUSTOM1"
	assert profile.technology() is None
	assert profile.job_kwargs({}) == {}
	assert profile.check_shots(10_000) is None
	assert profile.timeout_seconds({}) == 300.0
	assert profile.timeout_seconds({"timeout": "12"}) == 12.0
	assert profile.result_counts({"01": 3}, None) == {"01": 3}


# --- run_circuit goes through the profile -----------------------------------

class _Job:
	id = "job-7"
	queue_position = 2

	def __init__(self, counts):
		self.counts = counts

	def check(self):
		return "DONE"

	def get_counts(self):
		return self.counts


def _install_program_formats(monkeypatch):
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module(
		"mqt.core.qdmi",
		ProgramFormat=types.SimpleNamespace(
			IQM_JSON="fmt-iqm-json", QASM3="fmt-qasm3")))


def _running_driver(monkeypatch, counts, records, submitted):
	import util.iqm_transcode as iqm_transcode
	monkeypatch.setattr(
		iqm_transcode, "build_iqm_circuit",
		lambda *args, **kwargs: "iqm-circuit")
	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "extract_topology",
		lambda device: {"qubits": ["QB1"]})

	def to_result_record(counts, shots, provider, device_id, **kwargs):
		records.append((counts, shots, provider, device_id, kwargs))
		return {"schema": "qhw-result-v1"}

	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "to_result_record", to_result_record)
	_install_program_formats(monkeypatch)

	def submit_job(program, fmt, shots, **kwargs):
		submitted.append((program, fmt, shots, kwargs))
		return _Job(counts)

	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})
	driver._device = lambda credential=None: types.SimpleNamespace(submit_job=submit_job)
	driver._serialize_program = lambda iqm_circuit: "{}"
	return driver


def _circuit(cid="cid-9"):
	return types.SimpleNamespace(
		info={"qasm": "OPENQASM 2.0;", "num_shots": 10, "poll_interval": 0.0},
		get_cid=lambda: cid)


def test_task_lookups_find_each_cid_and_need_one(monkeypatch):
	# The driver used to keep only the last job, so a lookup without a cid
	# returned whoever ran last, and a lookup for an earlier cid failed
	# with an error naming the later one.
	driver = _running_driver(monkeypatch, {"1": 10}, [], [])
	driver.run_circuit(_circuit("cid-1"))
	driver.run_circuit(_circuit("cid-2"))

	assert driver.get_task_metadata("cid-1")["cid"] == "cid-1"
	assert driver.get_task_timing("cid-2")["cid"] == "cid-2"
	with pytest.raises(DEFwExecutionError, match="need the task's cid"):
		driver.get_task_metadata(None)
	with pytest.raises(DEFwExecutionError) as excinfo:
		driver.get_task_timing("cid-3")
	assert "cid-2" not in str(excinfo.value)


def test_run_circuit_opens_the_session_with_the_circuit_credential(
		monkeypatch):
	driver = _running_driver(monkeypatch, {"1": 10}, [], [])
	device = driver._device()
	seen = []

	def _device(credential=None):
		seen.append(credential)
		return device

	driver._device = _device
	circuit = _circuit()
	circuit.provider_credential = _bound_credential()

	driver.run_circuit(circuit)

	assert seen == [_bound_credential()]


def test_run_circuit_submits_what_the_profile_encodes(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"1": 10}, records, submitted)

	record = driver.run_circuit(_circuit())

	assert record == {"schema": "qhw-result-v1"}
	assert submitted == [("{}", "fmt-iqm-json", 10, {})]
	assert records == [({"1": 10}, 10, "iqm", "ornl-iqm-20q", {
		"job_id": "job-7", "status": "completed", "queue_position": 2})]
	assert driver.get_task_metadata("cid-9")["job_id"] == "job-7"


def test_run_circuit_keys_the_counts_through_the_profile(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"10": 7, "01": 3}, records, submitted)
	driver._profile.result_counts = (
		lambda counts, measurement: {k[::-1]: v for k, v in counts.items()})

	driver.run_circuit(_circuit())

	assert records[0][0] == {"01": 7, "10": 3}


def test_run_circuit_passes_the_profile_job_kwargs(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"1": 10}, records, submitted)
	driver._profile.job_kwargs = lambda info: {"custom1": "s3://bucket/run"}

	driver.run_circuit(_circuit())

	assert submitted[0][3] == {"custom1": "s3://bucket/run"}


def test_a_profile_shot_cap_stops_the_run_before_submission(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"1": 10}, records, submitted)

	def check_shots(shots):
		raise DEFwExecutionError(f"{shots} shots is over the device cap")

	driver._profile.check_shots = check_shots

	with pytest.raises(DEFwExecutionError, match="over the device cap"):
		driver.run_circuit(_circuit())

	assert submitted == []


def test_an_unknown_program_format_is_reported(monkeypatch):
	records, submitted = [], []
	driver = _running_driver(monkeypatch, {"1": 10}, records, submitted)
	driver._profile.encode = (
		lambda driver, source, info, device: ("prog", "NOT_A_FORMAT", None))

	with pytest.raises(
			DEFwExecutionError,
			match="knows no program format 'NOT_A_FORMAT'"):
		driver.run_circuit(_circuit())

	assert submitted == []


# --- fomac_normalize: the calibration slot and the technology ---------------

class _CustomDevice:
	def __init__(self, value="cal-set-1"):
		self.value = value
		self.queries = []

	def query_custom_property(self, slot, value_type):
		self.queries.append((slot, value_type))
		return self.value

	def regular_sites(self):
		return []

	def operations(self):
		return []


def _install_custom_property(monkeypatch):
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module(
		"mqt.core.qdmi",
		CustomProperty=types.SimpleNamespace(
			CUSTOM1="slot-1", CUSTOM2="slot-2")))


def test_the_calibration_slot_defaults_to_custom1(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _CustomDevice()

	assert fomac_normalize._calibration_set_id(device) == "cal-set-1"
	assert device.queries == [("slot-1", str)]


def test_a_profile_can_point_at_another_slot(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _CustomDevice()

	assert fomac_normalize._calibration_set_id(device, "CUSTOM2") == "cal-set-1"
	assert device.queries == [("slot-2", str)]


def test_no_slot_means_nothing_is_queried(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _CustomDevice()

	assert fomac_normalize._calibration_set_id(device, None) is None
	cal = fomac_normalize.extract_calibration(
		device, calibration_set_slot=None)

	assert cal["calibration_set_id"] is None
	assert device.queries == []


# --- svc_qpm tags each circuit with the resource's provider -----------------

def test_the_shim_qpm_tags_circuits_with_the_provider(monkeypatch):
	from svc_lib_qpm.svc_qpm import QPM
	import util.device_access as device_access

	device = {
		"provider": "aws",
		"provider-device-id": "arn:aws:braket:::device/quantum-simulator/amazon/sv1",
		"url": "https://braket.us-east-1.amazonaws.com",
		"credential-db": "creds.json",
	}
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: {"qpus": {"aws-sv1": device}})
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "aws-sv1")
	# The mock tests build a QPM without running __init__, and so does this.
	qpm = QPM.__new__(QPM)

	info = qpm.prepare_circuit({"qasm": "OPENQASM 2.0;"})

	assert info["qfw_backend"] == "aws"
	# Resolved once; a later circuit does not reread the config.
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: pytest.fail("config reread"))
	assert qpm.prepare_circuit({})["qfw_backend"] == "aws"


def test_a_device_record_carries_the_profile_technology():
	pytest.importorskip("qhw_data")
	topo = {"num_qubits": 2, "qubits": ["0", "1"], "edges": [["0", "1"]],
		"operations": {}}

	with_technology = fomac_normalize.to_device_record(
		topo, "aws", "aws-sv1", technology="simulator")
	without = fomac_normalize.to_device_record(topo, "iqm", "ornl-iqm-20q")

	assert with_technology["device"]["technology"] == "simulator"
	assert "technology" not in without["device"]
