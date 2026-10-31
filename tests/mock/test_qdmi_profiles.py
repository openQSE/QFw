# QFw delegates session opening to the packaged MQT Core-backed adapters.
# The remaining profiles carry QFw settings, policy and legacy output handling.

import pathlib
import sys
import types

import pytest

from tests.mock.fakes import stub_qdmi_session


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers import qdmi_normalize, qdmi_driver  # noqa: E402
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver  # noqa: E402
from svc_lib_qpm.drivers.qdmi_profiles import (  # noqa: E402
	IqmQdmiProfile, profile_for)


# --- choosing a profile ------------------------------------------------------

def test_the_default_profile_is_iqm():
	assert isinstance(profile_for({}), IqmQdmiProfile)
	assert isinstance(profile_for(None), IqmQdmiProfile)
	assert isinstance(profile_for({"provider": "IQM"}), IqmQdmiProfile)


def test_operation_loci_keep_ordered_three_qubit_tuples():
	sites = [types.SimpleNamespace(name=lambda n=n: n)
		for n in ["QB1", "QB2", "QB3", "QB3", "QB2", "QB1"]]
	op = types.SimpleNamespace(sites=lambda: sites, qubits_num=lambda: 3)
	assert qdmi_normalize._operation_loci(op) == [
		["QB1", "QB2", "QB3"], ["QB3", "QB2", "QB1"]]


@pytest.mark.parametrize("arity", [None, 0])
def test_unknown_arity_does_not_invent_site_tuples(arity):
	op = types.SimpleNamespace(sites=lambda: [object()], qubits_num=lambda: arity)
	assert qdmi_normalize._operation_loci(op) == []


def test_unsupported_arity_does_not_abort_introspection():
	def qubits_num():
		raise NotImplementedError
	op = types.SimpleNamespace(name=lambda: "measure", sites=lambda: [object()], qubits_num=qubits_num)
	device = types.SimpleNamespace(operations=lambda: [op])
	assert qdmi_normalize._operations(device) == {"measure": []}


@pytest.mark.parametrize("variable", [
	"IQM_TOKENS_FILE", "IQM_QC_ID", "IQM_QUANTUM_COMPUTER", "IQM_QC_ALIAS"])
def test_iqm_rejects_ambient_session_settings(monkeypatch, variable):
	monkeypatch.setenv(variable, "process-default")
	opened = []
	_install_iqm_backend(monkeypatch, opened)
	with pytest.raises(DEFwExecutionError, match=variable):
		profile_for({"provider": "iqm"}).open_backend({
			"base_url": "https://qc.example.org", "token": "user-token",
			"qc_alias": "reserved-device"})
	assert opened == []


def test_an_unknown_provider_fails_when_the_driver_is_built():
	with pytest.raises(
			DEFwExecutionError, match="no profile for provider 'ionq'"):
		QdmiDriver({"provider": "ionq"})


# --- the IQM profile: the session opens as it did before ---------------------

def _module(name, **attrs):
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	return module


def _install_iqm_backend(monkeypatch, opened):
	def IQMBackend(**kwargs):
		opened.append(kwargs)
		return types.SimpleNamespace(device="device-handle")
	monkeypatch.setitem(sys.modules, "iqm.qdmi.qiskit", _module(
		"iqm.qdmi.qiskit", IQMBackend=IQMBackend))


def test_the_iqm_session_uses_the_packaged_backend(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://qc.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "secret")
	opened = []

	def IQMBackend(**kwargs):
		opened.append(kwargs)
		return types.SimpleNamespace(device="device-handle")

	monkeypatch.setitem(sys.modules, "iqm.qdmi.qiskit", _module(
		"iqm.qdmi.qiskit", IQMBackend=IQMBackend))
	driver = QdmiDriver({
		"provider": "iqm", "id": "ornl-iqm-20q",
		"provider_device_id": "default"})

	assert driver._device() == "device-handle"
	assert opened == [{
		"base_url": "https://qc.example.org",
		"token": "secret", "qc_alias": "default"}]
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


@pytest.mark.parametrize("credential", [None, {"reservation_id": 7}])
def test_a_warm_session_does_not_resolve_access_again(monkeypatch, credential):
	monkeypatch.setenv("QFW_QC_URL", "https://qc.example.org")
	monkeypatch.setenv("QFW_API_KEY", "user-token")
	opened = []
	_install_iqm_backend(monkeypatch, opened)
	driver = QdmiDriver({"provider": "iqm"})
	device = driver._device(credential)
	monkeypatch.setattr(driver._profile, "access",
		lambda credential=None: pytest.fail("warm session resolved access"))

	assert driver._device(credential) is device
	assert len(opened) == 1


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
	_install_iqm_backend(monkeypatch, opened)
	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})

	driver._device(credential=_bound_credential())

	assert opened == [{
		"base_url": "https://qc.example.org",
		"token": "user-secret",
		"qc_alias": "default",
	}]


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
	_install_iqm_backend(monkeypatch, opened)
	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})
	alice = _bound_credential("alice", "alice-secret", reservation_id=7)
	bob = _bound_credential("bob", "bob-secret", reservation_id=8)

	driver._device(credential=alice)
	driver._device(credential=bob)
	driver._device(credential=alice)
	# The same user's next reservation is a session of its own.
	driver._device(credential=dict(alice, reservation_id=9))
	driver._device()

	assert [kwargs["token"] for kwargs in opened] == [
		"alice-secret", "bob-secret", "alice-secret", "service-secret"]


def test_an_ended_reservation_loses_its_session(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://service.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "service-secret")
	opened = []
	_install_iqm_backend(monkeypatch, opened)
	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})
	alice = _bound_credential("alice", "alice-secret", reservation_id=7)
	driver._device(credential=alice)
	driver._device()

	assert driver.evict_reservation(7) is True
	# Nothing left for it, and the service's own session is untouched.
	assert driver.evict_reservation(7) is False
	assert list(driver._backends) == [("default",)]
	# A circuit that still names the reservation opens a fresh session
	# rather than finding the old one.
	driver._device(credential=alice)
	assert len(opened) == 3


def test_a_missing_device_library_names_its_package(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://qc.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "secret")
	# A None entry makes `import iqm` fail the way an absent package does.
	monkeypatch.setitem(sys.modules, "iqm", None)
	monkeypatch.setitem(sys.modules, "iqm.qdmi.qiskit", None)

	with pytest.raises(DEFwExecutionError, match="iqm-qdmi"):
		QdmiDriver({"provider": "iqm"})._device()


def test_the_iqm_profile_encodes_an_iqm_json_program(monkeypatch):
	import util.iqm_transcode as iqm_transcode
	calls = []

	def build_iqm_circuit(source, dynamic, mapping):
		calls.append((source, dynamic, mapping))
		return "iqm-circuit"

	monkeypatch.setattr(iqm_transcode, "build_iqm_circuit", build_iqm_circuit)
	monkeypatch.setattr(
		qdmi_driver.qdmi_normalize, "extract_topology",
		lambda device: {"qubits": ["QB1", "QB2"]})
	driver = QdmiDriver({"provider": "iqm"})
	driver._serialize_program = lambda iqm_circuit: f"json:{iqm_circuit}"

	encoded = driver._profile.encode(
		driver, "OPENQASM 2.0;", {"qubit_mapping": {"q0": "QB2"}},
		types.SimpleNamespace(device=object()))

	# The format is a name, resolved by the driver only at submission.
	assert encoded == ("json:iqm-circuit", "IQM_JSON", None)
	assert calls == [
		("OPENQASM 2.0;", {"qubits": ["QB1", "QB2"]}, {"q0": "QB2"})]


# --- the IQM architecture rebuilt from QDMI ----------------------------------

def _qdmi_topology():
	# Shaped like extract_topology's output for the q20, cut to three qubits.
	return {
		"num_qubits": 3,
		"qubits": ["QB1", "QB2", "QB3"],
		"edges": [["QB1", "QB2"], ["QB2", "QB3"]],
		"operations": {
			"prx": [["QB1"], ["QB2"], ["QB3"]],
			"cz": [["QB1", "QB2"], ["QB2", "QB3"]],
			"measure": [["QB1"], ["QB2"], ["QB3"]],
		},
	}


def test_the_iqm_architecture_is_rebuilt_from_qdmi_topology():
	from svc_lib_qpm.drivers.qdmi_profiles import iqm_architecture

	arch = iqm_architecture(_qdmi_topology(), "cal-set-1")

	assert arch["calibration_set_id"] == "cal-set-1"
	assert arch["qubits"] == ["QB1", "QB2", "QB3"]
	assert arch["computational_resonators"] == []
	assert sorted(arch["gates"]) == ["cz", "measure", "prx"]
	assert arch["gates"]["cz"] == {
		"implementations": {"qdmi": {"loci": [["QB1", "QB2"], ["QB2", "QB3"]]}},
		"default_implementation": "qdmi",
		"override_default_implementation": {},
	}


def test_without_a_calibration_set_only_the_qubits_are_passed():
	# The architecture requires one, so there is nothing to transpile against
	# and build_iqm_circuit serializes an already-native circuit, as before.
	from svc_lib_qpm.drivers.qdmi_profiles import iqm_architecture

	assert iqm_architecture(_qdmi_topology(), None) == {
		"qubits": ["QB1", "QB2", "QB3"]}


def test_without_a_measure_gate_only_the_qubits_are_passed():
	# IQMBackendBase reads the measure gate's loci and raises a KeyError,
	# which build_iqm_circuit would not fall back on, when there is none.
	from svc_lib_qpm.drivers.qdmi_profiles import iqm_architecture

	topology = _qdmi_topology()
	del topology["operations"]["measure"]

	assert iqm_architecture(topology, "cal-set-1") == {
		"qubits": ["QB1", "QB2", "QB3"]}


def test_a_locus_outside_the_qubits_means_only_the_qubits_are_passed():
	# A computational resonator. QDMI does not say which sites are
	# resonators, so a device with them is not rebuilt.
	from svc_lib_qpm.drivers.qdmi_profiles import iqm_architecture

	topology = _qdmi_topology()
	topology["operations"]["move"] = [["QB1", "CR1"]]

	assert iqm_architecture(topology, "cal-set-1") == {
		"qubits": ["QB1", "QB2", "QB3"]}


def test_the_iqm_profile_transcodes_against_the_rebuilt_architecture(
		monkeypatch):
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers.qdmi_profiles import iqm_architecture
	calls = []
	slots = []

	def build_iqm_circuit(source, dynamic, mapping):
		calls.append(dynamic)
		return "iqm-circuit"

	def calibration_set_id(device, slot):
		slots.append(slot)
		return "cal-set-1"

	monkeypatch.setattr(iqm_transcode, "build_iqm_circuit", build_iqm_circuit)
	monkeypatch.setattr(
		qdmi_driver.qdmi_normalize, "extract_topology",
		lambda device: _qdmi_topology())
	monkeypatch.setattr(
		qdmi_driver.qdmi_normalize, "_calibration_set_id",
		calibration_set_id)
	driver = QdmiDriver({"provider": "iqm"})
	driver._serialize_program = lambda iqm_circuit: f"json:{iqm_circuit}"

	driver._profile.encode(driver, "OPENQASM 2.0;", {}, types.SimpleNamespace(device=object()))

	assert slots == ["CUSTOM1"]
	assert calls == [iqm_architecture(_qdmi_topology(), "cal-set-1")]


def test_the_iqm_profile_defaults():
	profile = profile_for({"provider": "iqm"})

	assert profile.calibration_set_slot == "CUSTOM1"
	assert profile.technology() == "superconducting"
	assert profile.job_kwargs({}) == {}
	assert profile.check_shots(10_000) is None
	assert profile.timeout_seconds({}) == 300.0
	assert profile.timeout_seconds({"timeout": "12"}) == 12.0
	assert profile.result_counts({"01": 3}, None) == {"01": 3}


@pytest.mark.parametrize("provider", ["iqm", "aws", "ibm"])
def test_resource_shot_caps_apply_to_every_provider(provider):
	profile = profile_for({"provider": provider, "id": "limited-device", "max_shots": "10"})
	assert profile.check_shots(10) is None
	with pytest.raises(DEFwExecutionError, match="over the 10-shot cap"):
		profile.check_shots(11)
	assert profile_for({"provider": provider}).check_shots(10**6) is None


@pytest.mark.parametrize("provider", ["iqm", "aws", "ibm"])
def test_resource_timeouts_allow_a_client_override(provider):
	profile = profile_for({"provider": provider, "job_timeout_seconds": "120"})
	assert profile.timeout_seconds({}) == 120.0
	assert profile.timeout_seconds({"timeout": "12"}) == 12.0
	assert profile_for({"provider": provider}).timeout_seconds({}) == 300.0


def test_amazon_braket_keeps_its_zero_shot_policy():
	assert profile_for({"provider": "aws", "max_shots": "10"}).check_shots(0) is None


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
		qdmi_driver.qdmi_normalize, "extract_topology",
		lambda device: {"qubits": ["QB1"]})

	def to_result_record(counts, shots, provider, device_id, **kwargs):
		records.append((counts, shots, provider, device_id, kwargs))
		return {"schema": "qhw-result-v1"}

	monkeypatch.setattr(
		qdmi_driver.qdmi_normalize, "to_result_record", to_result_record)
	_install_program_formats(monkeypatch)

	def submit_job(program, fmt, shots, **kwargs):
		submitted.append((program, fmt, shots, kwargs))
		return _Job(counts)

	driver = QdmiDriver({"provider": "iqm", "id": "ornl-iqm-20q"})
	stub_qdmi_session(driver, types.SimpleNamespace(submit_job=submit_job))
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
	backend = driver._backend()
	seen = []

	def _backend(credential=None):
		seen.append(credential)
		return backend

	driver._backend = _backend
	circuit = _circuit()
	circuit.provider_credential = _bound_credential()

	driver.run_circuit(circuit)

	assert seen == [_bound_credential()]


@pytest.mark.parametrize("info,device,expected", [
	# What QFwBackend sends: the device's first wait, doubling from there.
	({}, {"poll_interval_seconds": 4}, (4.0, True)),
	({}, {}, (1.0, True)),
	# A client that names its interval gets that, fixed.
	({"poll_interval": 0.5}, {"poll_interval_seconds": 4}, (0.5, False)),
])
def test_run_circuit_polls_on_the_poll_schedule(
		monkeypatch, info, device, expected):
	driver = _running_driver(monkeypatch, {"1": 10}, [], [])
	driver._descriptor.update(device)
	seen = []

	def _poll_job(job, timeout, poll, cancel_event=None, backoff=False):
		seen.append((poll, backoff))
		return "completed"

	driver._poll_job = _poll_job
	circuit = _circuit()
	del circuit.info["poll_interval"]
	circuit.info.update(info)

	driver.run_circuit(circuit)

	assert seen == [expected]


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


# --- qdmi_normalize: the calibration slot and the technology ---------------

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

	assert qdmi_normalize._calibration_set_id(device) == "cal-set-1"
	assert device.queries == [("slot-1", str)]


def test_a_profile_can_point_at_another_slot(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _CustomDevice()

	assert qdmi_normalize._calibration_set_id(device, "CUSTOM2") == "cal-set-1"
	assert device.queries == [("slot-2", str)]


def test_no_slot_means_nothing_is_queried(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _CustomDevice()

	assert qdmi_normalize._calibration_set_id(device, None) is None
	cal = qdmi_normalize.extract_calibration(
		device, calibration_set_slot=None)

	assert cal["calibration_set_id"] is None
	assert device.queries == []


@pytest.mark.parametrize("scale, expected_scale, exception", [
	(0.001, 0.001, None), (1.0, 1.0, None), (None, 1.0, None),
	("missing", 1.0, None), ("failure", None, RuntimeError),
	(0.0, None, DEFwExecutionError), (-1.0, None, DEFwExecutionError),
	(float("inf"), None, DEFwExecutionError), (float("nan"), None, DEFwExecutionError)])
def test_calibration_times_use_the_device_duration_scale(scale, expected_scale, exception):
	site = types.SimpleNamespace(
		name=lambda: "0", t1=lambda: 10000, t2=lambda: 8000)
	unavailable = types.SimpleNamespace(name=lambda: "1", t1=lambda: None)
	op = types.SimpleNamespace(
		name=lambda: "x", qubits_num=lambda: 1, sites=lambda: [site],
		fidelity=lambda **kwargs: 0.99, duration=lambda **kwargs: 40)
	device = types.SimpleNamespace(
		regular_sites=lambda: [site, unavailable], operations=lambda: [op],
		duration_unit=lambda: "us")

	def duration_scale_factor():
		if scale == "failure":
			raise RuntimeError("query failed")
		return scale

	if scale != "missing":
		device.duration_scale_factor = duration_scale_factor
	if exception is not None:
		with pytest.raises(exception):
			qdmi_normalize.extract_calibration(device, calibration_set_slot=None)
		return

	cal = qdmi_normalize.extract_calibration(device, calibration_set_slot=None)
	assert cal["qubit_metrics"] == [
		{"qubit": "0", "t1": 10000 * expected_scale, "t2": 8000 * expected_scale},
		{"qubit": "1"}]
	assert cal["gate_metrics"] == [{
		"operation": "x", "locus": ["0"], "fidelity": 0.99,
		"duration": 40 * expected_scale}]
	assert cal["duration_unit"] == "us"


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

	with_technology = qdmi_normalize.to_device_record(
		topo, "aws", "aws-sv1", technology="simulator")
	without = qdmi_normalize.to_device_record(topo, "iqm", "ornl-iqm-20q")

	assert with_technology["device"]["technology"] == "simulator"
	assert "technology" not in without["device"]


def test_topology_and_calibration_share_three_qubit_loci():
	sites = [types.SimpleNamespace(name=lambda i=i: str(i)) for i in range(3)]
	queried = []

	def fidelity(*, sites):
		queried.append(tuple(site.name() for site in sites))
		return 0.9

	op = types.SimpleNamespace(
		name=lambda: "ccnot", qubits_num=lambda: 3,
		sites=lambda: sites, fidelity=fidelity)
	device = types.SimpleNamespace(operations=lambda: [op])
	assert qdmi_normalize._operations(device) == {"ccnot": [["0", "1", "2"]]}
	assert qdmi_normalize._operation_metrics(device, 1.0) == [{
		"operation": "ccnot", "locus": ["0", "1", "2"], "fidelity": 0.9}]
	assert queried == [("0", "1", "2")]


def test_incomplete_site_tuples_are_rejected():
	op = types.SimpleNamespace(qubits_num=lambda: 3, sites=lambda: [0, 1])
	with pytest.raises(DEFwExecutionError, match="incomplete"):
		qdmi_normalize._operation_loci(op)


def test_analog_capacity_does_not_invent_fixed_sites():
	device = types.SimpleNamespace(
		regular_sites=lambda: [], qubits_num=lambda: 100,
		operations=lambda: [], coupling_map=lambda: [])
	topology = qdmi_normalize.extract_topology(device)
	assert topology == {"num_qubits": 100, "qubits": [], "edges": [], "operations": {}}
	pytest.importorskip("qhw_data")
	record = qdmi_normalize.to_device_record(topology, "pasqal", "emu-free", technology="simulator")
	assert record["device"]["num_qubits"] == 100
	assert record["qubits"] == []
