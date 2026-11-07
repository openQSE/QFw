# The contract for a circuit compiled ahead of time (util.isa): what a QPM
# declares and refuses, and what the Qiskit client sends. The transcoding itself
# needs real Qiskit and is in tests/qiskit/test_isa_circuits.py.

import logging
import sys
import types

import pytest

from defw_exception import DEFwExecutionError
from tests.mock.fakes import FakeEventAPI, FakeQPM, FakeSlurmDriver
from util import device_access, isa


DEVICE = "q20"
CALIBRATION = {"kind": "set-id", "value": "cal-1"}


def _marker(**fields):
	marker = isa.make_marker(
		"iqm", DEVICE, calibration=CALIBRATION, qubit_labels=["QB1", "QB2"])
	marker.update(fields)
	return marker


def _info(**fields):
	info = {
		"isa": _marker(),
		"circuit": {"format": "qpy+gzip", "data": "AAAA"},
	}
	info.update(fields)
	return info


STAGES = [isa.STAGE_GENERIC, isa.STAGE_ISA]


# --- the marker --------------------------------------------------------------

def test_a_marker_survives_being_read_back():
	marker = _marker(compiler="qiskit 2.5.2")

	assert isa.read_marker({"isa": marker}) == marker


def test_a_marker_refuses_a_qubit_named_twice():
	with pytest.raises(DEFwExecutionError, match="QB1"):
		isa.make_marker("iqm", DEVICE, qubit_labels=["QB1", "QB2", "QB1"])


def test_index_labels_pass_a_device_addressed_by_index():
	isa.check_index_labels(isa.make_marker("ibm", DEVICE))
	isa.check_index_labels(
		isa.make_marker("ibm", DEVICE, qubit_labels=[0, 1, 2]))


def test_other_labels_are_refused_on_a_device_addressed_by_index():
	marker = isa.make_marker("ibm", DEVICE, qubit_labels=[2, 0, 1])

	with pytest.raises(DEFwExecutionError, match="by index"):
		isa.check_index_labels(marker)


def test_no_marker_is_a_generic_circuit():
	assert isa.requested({"qasm": "OPENQASM 2.0;"}) is False
	assert isa.read_marker({"qasm": "OPENQASM 2.0;"}) is None


def test_a_marker_with_no_calibration_says_so():
	marker = isa.make_marker("ibm", "ibm_torino")

	assert marker["calibration"] == {"kind": "none", "value": None}


@pytest.mark.parametrize("calibration", [
	{"kind": "guess", "value": "x"},
	{"kind": "set-id", "value": None},
	"cal-1",
])
def test_a_marker_refuses_a_calibration_it_cannot_compare(calibration):
	with pytest.raises(DEFwExecutionError, match="calibration"):
		isa.make_marker("iqm", DEVICE, calibration=calibration)


@pytest.mark.parametrize("marker", ["isa", {"v": 2}, {"v": 1}])
def test_a_marker_this_qpm_cannot_read_is_refused(marker):
	with pytest.raises(DEFwExecutionError):
		isa.read_marker({"isa": marker})


# --- stages ------------------------------------------------------------------

@pytest.mark.parametrize("provider", ["iqm", "IBM"])
def test_a_provider_that_runs_a_circuit_unchanged_takes_isa(provider):
	assert isa.STAGE_ISA in isa.circuit_stages(provider)


@pytest.mark.parametrize("provider", ["aws", "pasqal", "nwqsim", None])
def test_a_provider_that_compiles_for_itself_does_not_take_isa(provider):
	assert isa.circuit_stages(provider) == [isa.STAGE_GENERIC]


# --- check_envelope ----------------------------------------------------------

def test_the_envelope_passes_a_well_formed_request():
	assert isa.check_envelope(_info(), STAGES, DEVICE, "iqm") == _marker()


def test_the_envelope_ignores_a_generic_circuit():
	assert isa.check_envelope({"qasm": "x"}, ["generic"], None, None) is None


def test_the_envelope_refuses_a_qpm_that_does_not_take_isa():
	with pytest.raises(DEFwExecutionError, match="compiled ahead of time"):
		isa.check_envelope(_info(), [isa.STAGE_GENERIC], DEVICE, "iqm")


def test_the_envelope_refuses_another_device():
	with pytest.raises(DEFwExecutionError, match="compiled for device"):
		isa.check_envelope(_info(), STAGES, "q54", "iqm")


def test_the_envelope_refuses_another_provider():
	with pytest.raises(DEFwExecutionError, match="'iqm'.*'ibm'"):
		isa.check_envelope(_info(), STAGES, DEVICE, "ibm")


def test_the_envelope_reads_the_provider_in_any_case():
	assert isa.check_envelope(_info(), STAGES, DEVICE, "IQM")


def test_the_envelope_refuses_openqasm():
	info = _info(circuit=None, qasm="OPENQASM 2.0;")

	with pytest.raises(DEFwExecutionError, match="QPY"):
		isa.check_envelope(info, STAGES, DEVICE, "iqm")


def test_the_envelope_refuses_compilation_options():
	info = _info(compilation_options={"optimization_level": 3})

	with pytest.raises(DEFwExecutionError, match="compilation_options"):
		isa.check_envelope(info, STAGES, DEVICE, "iqm")


# --- check_calibration -------------------------------------------------------

def test_the_calibration_it_was_compiled_for_runs():
	isa.check_calibration(_marker(), "cal-1")


def test_a_set_id_requires_its_calibration_by_default():
	with pytest.raises(DEFwExecutionError, match="compile it again"):
		isa.check_calibration(_marker(), "cal-2")


def test_a_set_id_that_cannot_be_compared_is_refused():
	with pytest.raises(DEFwExecutionError, match="compile it again"):
		isa.check_calibration(_marker(), None)


def test_the_device_can_run_on_the_current_calibration(caplog):
	with caplog.at_level(logging.WARNING):
		isa.check_calibration(_marker(), "cal-2", isa.POLICY_CURRENT)

	assert "cal-2" in caplog.text


def test_a_properties_time_only_warns_by_default(caplog):
	marker = _marker(calibration={
		"kind": "properties-time", "value": "2026-10-30T00:00:00Z"})

	with caplog.at_level(logging.WARNING):
		isa.check_calibration(marker, "2026-10-30T06:00:00Z")

	assert "2026-10-30T06:00:00Z" in caplog.text


def test_the_device_can_require_a_properties_time():
	marker = _marker(calibration={"kind": "properties-time", "value": "t1"})

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		isa.check_calibration(marker, "t2", isa.POLICY_REQUIRE)


def test_a_circuit_with_no_calibration_is_not_compared():
	isa.check_calibration(_marker(calibration=None), "cal-2")


def test_a_device_that_requires_a_calibration_refuses_a_circuit_naming_none():
	with pytest.raises(DEFwExecutionError, match="names none"):
		isa.check_calibration(
			_marker(calibration=None), "cal-2", isa.POLICY_REQUIRE)


def test_a_policy_it_does_not_know_is_refused():
	with pytest.raises(DEFwExecutionError, match="calibration-policy"):
		isa.check_calibration(_marker(), "cal-2", "pin")


def test_device_access_carries_the_calibration_policy(monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	device = {
		"provider": "iqm", "url": "https://example.org/",
		"credential-db": "creds.json", "calibration-policy": "current",
	}

	selected = device_access.select_qpu(
		{"qpus": {DEVICE: device}}, "cfg.yaml", provider="iqm")

	assert selected["calibration-policy"] == "current"


@pytest.mark.parametrize("policy", ["required", {"set-id": "require"}])
def test_device_access_refuses_a_calibration_policy_it_does_not_know(
		monkeypatch, policy):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	device = {
		"provider": "iqm", "url": "https://example.org/",
		"credential-db": "creds.json", "calibration-policy": policy,
	}

	with pytest.raises(DEFwExecutionError, match="calibration-policy"):
		device_access.select_qpu(
			{"qpus": {DEVICE: device}}, "cfg.yaml", provider="iqm")


# --- the QPMs ----------------------------------------------------------------

def _base_qpm():
	from util.qpm.util_qpm import UTIL_QPM
	return UTIL_QPM.__new__(UTIL_QPM)


def test_a_qpm_declares_generic_circuits_only_unless_it_says_otherwise():
	assert _base_qpm().circuit_stages() == [isa.STAGE_GENERIC]


def test_a_qpm_that_does_not_take_isa_refuses_it_before_registering_it():
	with pytest.raises(DEFwExecutionError, match="compiled ahead of time"):
		_base_qpm().create_circuit(_info())


@pytest.mark.parametrize("device, provider, refusal", [
	("q54", "iqm", "'q54'"),
	(DEVICE, "ibm", "'ibm'"),
])
def test_a_qpm_compares_its_own_device_and_provider(device, provider, refusal):
	from util.qpm.util_qpm import UTIL_QPM

	class QPM(UTIL_QPM):
		def circuit_stages(self):
			return STAGES

		def isa_device_id(self):
			return device

		def isa_provider(self):
			return provider

	with pytest.raises(DEFwExecutionError, match=refusal):
		QPM.__new__(QPM).create_circuit(_info())


def test_a_qpm_refuses_a_calibration_set_it_cannot_run_on():
	info = {"calibration_set_id": "cal-0", "qasm": "OPENQASM 2.0;"}

	with pytest.raises(DEFwExecutionError, match="chosen calibration set"):
		_base_qpm().create_circuit(info)


def test_the_native_iqm_qpm_runs_on_a_chosen_calibration():
	from svc_iqm_qpm.svc_qpm import QPM

	assert QPM.__new__(QPM).runs_on_a_chosen_calibration({}) is True


def _shim_routing_runs_to(provider, library):
	from svc_lib_qpm.svc_qpm import QPM
	qpm = QPM.__new__(QPM)
	qpm._descriptor_provider = provider
	routed = []

	def route(call, lib=None):
		routed.append((call, lib))
		return types.SimpleNamespace(name=library or lib)

	qpm.qrc = types.SimpleNamespace(
		frontend=types.SimpleNamespace(route=route))
	return qpm, routed


@pytest.mark.parametrize("provider, library, runs", [
	("iqm", "qrmi", True),
	("iqm", "qdmi", False),
	("ibm", "qrmi", False),
])
def test_the_shim_runs_on_a_chosen_calibration_only_for_iqm_through_qrmi(
		provider, library, runs):
	qpm, routed = _shim_routing_runs_to(provider, library)

	assert qpm.runs_on_a_chosen_calibration({"lib": "qdmi"}) is runs
	assert routed == [("run_circuit", "qdmi")]


def test_the_shim_publishes_the_device_calibration_policy(monkeypatch):
	import svc_lib_qpm.svc_qpm as svc_qpm
	import svc_lib_qpm.descriptor as descriptor
	seen = {}
	monkeypatch.setattr(descriptor, "resolve_descriptor", lambda: {
		"id": DEVICE, "provider": "iqm", "calibration_policy": "require"})
	qpm = svc_qpm.QPM.__new__(svc_qpm.QPM)
	qpm.query_helper = lambda *args, properties: seen.update(properties)

	qpm.query()

	assert seen["calibration_policy"] == "require"


@pytest.mark.parametrize("served_by, published", [
	(["qrmi"], True), ([], None),
])
def test_the_shim_publishes_whether_it_serves_the_dynamic_architecture(
		monkeypatch, served_by, published):
	import svc_lib_qpm.svc_qpm as svc_qpm
	import svc_lib_qpm.descriptor as descriptor
	seen = {}
	monkeypatch.setattr(descriptor, "resolve_descriptor", lambda: {
		"id": DEVICE, "provider": "iqm"})
	qpm = svc_qpm.QPM.__new__(svc_qpm.QPM)
	qpm.qrc = types.SimpleNamespace(frontend=types.SimpleNamespace(
		description_library=lambda: None,
		capability_map=lambda: {"get_dynamic_backend_info": served_by}))
	qpm.query_helper = lambda *args, properties: seen.update(properties)

	qpm.query()

	assert seen.get("dynamic_architecture") is published


def test_the_architecture_is_read_with_the_reservations_credential(monkeypatch):
	from tests.mock import test_pulser_qpm as pulser_tests
	controller = pulser_tests._Controller()
	qpm = pulser_tests._lib_qpm(monkeypatch, "iqm")
	qpm.controller = controller
	qpm.qrc = types.SimpleNamespace(
		get_dynamic_backend_info=lambda calibration_set_id=None, lib=None,
		credential=None: credential)

	assert qpm.get_dynamic_backend_info(reservation_id="7") == {
		"token": "alice-tok", "project_id": "proj-alice",
		"reservation_id": 7}
	assert controller.asked[-1] == (7, "get_dynamic_backend_info")
	assert qpm.get_dynamic_backend_info() is None


def test_the_architecture_credential_reaches_the_qrmi_driver():
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	from svc_lib_qpm.frontend import Frontend
	descriptor = {
		"provider": "iqm", "id": DEVICE, "libraries": ["qrmi"],
		"caps": {"get_dynamic_backend_info": ["qrmi"]}}
	driver = QrmiDriver(descriptor)
	read = []
	driver._read_target = lambda credential=None: read.append(credential) or (
		_iqm_target("cal-1"))

	Frontend([driver], descriptor).get_dynamic_backend_info(
		credential={"reservation_id": 7})

	assert read == [{"reservation_id": 7}]


def test_the_native_iqm_qpm_serves_the_dynamic_architecture(monkeypatch):
	from svc_iqm_qpm.svc_qpm import QPM
	qpm = QPM.__new__(QPM)
	seen = {}
	qpm.query_helper = lambda *args, properties: seen.update(properties)

	qpm.query()

	assert seen["dynamic_architecture"] is True


def test_the_native_iqm_qpm_does_not_take_isa():
	from svc_iqm_qpm.svc_qpm import QPM
	qpm = QPM.__new__(QPM)

	assert qpm.circuit_stages() == [isa.STAGE_GENERIC]


def test_the_shim_declares_isa_by_the_device_it_serves():
	from svc_lib_qpm.svc_qpm import QPM
	iqm, aws = QPM.__new__(QPM), QPM.__new__(QPM)
	iqm._descriptor_provider = "iqm"
	aws._descriptor_provider = "aws"

	assert isa.STAGE_ISA in iqm.circuit_stages()
	assert aws.circuit_stages() == [isa.STAGE_GENERIC]


def test_the_shim_compares_the_device_it_publishes(monkeypatch):
	from svc_lib_qpm.svc_qpm import QPM
	monkeypatch.setenv("QFW_QPU_DEVICE_ID", DEVICE)

	assert QPM.__new__(QPM).isa_device_id() == DEVICE


def test_the_shim_reads_its_device_id_once(monkeypatch):
	import svc_lib_qpm.descriptor as descriptor
	from svc_lib_qpm.svc_qpm import QPM
	monkeypatch.delenv("QFW_QPU_DEVICE_ID", raising=False)
	reads = []
	monkeypatch.setattr(
		descriptor, "resolve_descriptor",
		lambda: reads.append(1) or {"id": DEVICE})
	qpm = QPM.__new__(QPM)

	assert [qpm.isa_device_id() for _ in range(3)] == [DEVICE] * 3
	assert len(reads) == 1


def test_the_shim_compares_the_provider_of_its_device():
	from svc_lib_qpm.svc_qpm import QPM
	qpm = QPM.__new__(QPM)
	qpm._descriptor_provider = "iqm"

	assert qpm.isa_provider() == "iqm"


def _fake_iqm():
	from svc_fake_iqm_qpm.svc_qpm import QPM
	return QPM.__new__(QPM)


def test_fake_iqm_takes_isa_for_its_own_device(monkeypatch):
	from svc_fake_iqm_qpm.svc_qpm import (
		FAKE_IQM_CALIBRATION_ENV, FAKE_IQM_PROVIDER, FAKE_IQM_TARGET_ID)
	monkeypatch.setenv(FAKE_IQM_CALIBRATION_ENV, "cal-1")
	qpm = _fake_iqm()
	info = _info(isa=_marker(
		provider=FAKE_IQM_PROVIDER, device_id=FAKE_IQM_TARGET_ID))

	assert isa.check_envelope(
		info, qpm.circuit_stages(), qpm.isa_device_id(), qpm.isa_provider())
	assert qpm.prepare_circuit(info)["isa"] == info["isa"]


def test_fake_iqm_refuses_a_stale_calibration_before_it_runs(monkeypatch):
	from svc_fake_iqm_qpm.svc_qpm import FAKE_IQM_CALIBRATION_ENV
	monkeypatch.setenv(FAKE_IQM_CALIBRATION_ENV, "cal-2")

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		_fake_iqm().prepare_circuit(_info())


# --- the drivers -------------------------------------------------------------

class _Stop(Exception):
	pass


def _source(**info):
	return types.SimpleNamespace(info=info, provider_credential=None)


def _no_compile(*args, **kwargs):
	raise AssertionError("a circuit compiled ahead of time was compiled again")


def _iqm_target(calibration_set_id):
	return {"dynamic_quantum_architecture": {
		"calibration_set_id": calibration_set_id}}


def test_qrmi_iqm_sends_a_marked_circuit_through_the_isa_path(monkeypatch):
	import util.circuit_payload as circuit_payload
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	seen = []

	def build(circuit, dynamic, marker):
		seen.append((circuit, marker))
		raise _Stop()

	monkeypatch.setattr(iqm_transcode, "build_iqm_circuit", _no_compile)
	monkeypatch.setattr(iqm_transcode, "build_isa_iqm_circuit", build)
	monkeypatch.setattr(circuit_payload, "qiskit_input", lambda info: "isa")
	driver = QrmiDriver({"provider": "iqm"})
	driver._resource = lambda: object()
	driver._read_target = lambda credential=None: _iqm_target("cal-1")

	with pytest.raises(_Stop):
		driver._run_iqm_circuit(_source(**_info()))

	assert seen == [("isa", _marker())]


def test_qrmi_iqm_refuses_a_stale_calibration(monkeypatch):
	import util.circuit_payload as circuit_payload
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	monkeypatch.setattr(iqm_transcode, "build_isa_iqm_circuit", _no_compile)
	monkeypatch.setattr(circuit_payload, "qiskit_input", lambda info: "isa")
	driver = QrmiDriver({"provider": "iqm"})
	driver._resource = lambda: object()
	driver._read_target = lambda credential=None: _iqm_target("cal-2")

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		driver._run_iqm_circuit(_source(**_info()))


@pytest.mark.parametrize("field", ["calibration_set_id", "iqm_calibration_set_id"])
def test_qrmi_iqm_refuses_a_job_pinned_to_another_calibration(monkeypatch, field):
	import util.circuit_payload as circuit_payload
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	monkeypatch.setattr(iqm_transcode, "build_isa_iqm_circuit", _no_compile)
	monkeypatch.setattr(circuit_payload, "qiskit_input", lambda info: "isa")
	driver = QrmiDriver({"provider": "iqm"})
	driver._resource = lambda: object()
	driver._read_target = lambda credential=None: _iqm_target("cal-1")

	with pytest.raises(DEFwExecutionError, match="cal-9"):
		driver._run_iqm_circuit(_source(**_info(**{field: "cal-9"})))


def test_pinning_the_compiled_calibration_does_not_excuse_a_recalibration(
		monkeypatch):
	import util.circuit_payload as circuit_payload
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	monkeypatch.setattr(iqm_transcode, "build_isa_iqm_circuit", _no_compile)
	monkeypatch.setattr(circuit_payload, "qiskit_input", lambda info: "isa")
	driver = QrmiDriver({"provider": "iqm"})
	driver._resource = lambda: object()
	driver._read_target = lambda credential=None: _iqm_target("cal-2")

	with pytest.raises(DEFwExecutionError, match="cal-2"):
		driver._run_iqm_circuit(_source(**_info(calibration_set_id="cal-1")))


def _driver_holding_a_cached_target(monkeypatch, provider, cached, **descriptor):
	import util.circuit_payload as circuit_payload
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	monkeypatch.setattr(iqm_transcode, "build_isa_iqm_circuit", _no_compile)
	monkeypatch.setattr(circuit_payload, "qiskit_input", lambda info: "isa")
	driver = QrmiDriver(dict(descriptor, provider=provider))
	driver._resource = lambda: object()
	driver._read_target = lambda credential=None: cached
	assert driver._target() == cached
	_age_reads(driver, _read_window())
	return driver


def _read_window():
	from svc_lib_qpm.drivers.qrmi_driver import REQUIRED_CALIBRATION_READ_SECONDS
	return REQUIRED_CALIBRATION_READ_SECONDS


def _age_reads(driver, seconds):
	driver._target_fetched_at = {
		key: fetched_at - seconds
		for key, fetched_at in driver._target_fetched_at.items()}


def test_a_required_calibration_is_read_once_for_circuits_close_together(
		monkeypatch):
	driver = _driver_holding_a_cached_target(
		monkeypatch, "iqm", _iqm_target("cal-1"))
	reads = []
	driver._read_target = lambda credential=None: (
		reads.append(credential) or _iqm_target("cal-1"))

	for _ in range(3):
		driver._refresh_target_when_required(_source(**_info()))
	assert len(reads) == 1

	_age_reads(driver, _read_window())
	driver._refresh_target_when_required(_source(**_info()))
	assert len(reads) == 2


def test_a_cache_kept_through_a_failed_read_does_not_confirm_a_calibration(
		monkeypatch):
	driver = _driver_holding_a_cached_target(
		monkeypatch, "iqm", _iqm_target("cal-1"))
	_age_reads(driver, driver._target_ttl_seconds)

	def unreachable(credential=None):
		raise DEFwExecutionError("device unreachable")

	driver._read_target = unreachable
	driver._target()

	with pytest.raises(DEFwExecutionError, match="device unreachable"):
		driver._refresh_target_when_required(_source(**_info()))


def test_a_required_calibration_is_read_past_the_cache(monkeypatch):
	driver = _driver_holding_a_cached_target(
		monkeypatch, "iqm", _iqm_target("cal-1"))
	driver._read_target = lambda credential=None: _iqm_target("cal-2")

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		driver._run_iqm_circuit(_source(**_info()))


def test_a_required_calibration_is_not_taken_from_a_cache_it_cannot_refresh(
		monkeypatch):
	driver = _driver_holding_a_cached_target(
		monkeypatch, "iqm", _iqm_target("cal-1"))

	def unreachable(credential=None):
		raise DEFwExecutionError("device unreachable")

	driver._read_target = unreachable

	with pytest.raises(DEFwExecutionError, match="device unreachable"):
		driver._run_iqm_circuit(_source(**_info()))


def _ibm_info(**fields):
	marker = isa.make_marker(
		"ibm", DEVICE, calibration={"kind": "properties-time", "value": "t1"})
	return _info(isa=marker, **fields)


def _ibm_target(properties_time):
	return {"configuration": {"n_qubits": 2},
		"properties": {"last_update_date": properties_time}}


def test_qrmi_ibm_reads_a_required_calibration_before_it_builds_a_target(
		monkeypatch):
	driver = _driver_holding_a_cached_target(
		monkeypatch, "ibm", _ibm_target("t1"), calibration_policy="require")

	def unreachable(credential=None):
		raise DEFwExecutionError("device unreachable")

	driver._read_target = unreachable
	driver._build_backend_target = _no_compile

	with pytest.raises(DEFwExecutionError, match="device unreachable"):
		driver._run_ibm_circuit(_source(**_ibm_info()))


def test_an_incomplete_read_of_a_required_calibration_is_refused(monkeypatch):
	driver = _driver_holding_a_cached_target(
		monkeypatch, "ibm", _ibm_target("t1"), calibration_policy="require")
	driver._read_target = lambda credential=None: {
		"configuration": None, "properties": None}

	with pytest.raises(DEFwExecutionError, match="came back incomplete"):
		driver._refresh_target_when_required(_source(**_ibm_info()))

	assert driver._target() == _ibm_target("t1")


def test_a_refreshed_required_calibration_is_the_one_later_reads_see(monkeypatch):
	driver = _driver_holding_a_cached_target(
		monkeypatch, "ibm", _ibm_target("t1"), calibration_policy="require")
	driver._read_target = lambda credential=None: _ibm_target("t2")

	driver._refresh_target_when_required(_source(**_ibm_info()))

	driver._read_target = _no_compile
	assert driver._target() == _ibm_target("t2")


def test_the_cache_serves_a_calibration_that_is_not_required(monkeypatch):
	driver = _driver_holding_a_cached_target(
		monkeypatch, "ibm", _ibm_target("t1"))
	driver._read_target = _no_compile

	driver._refresh_target_when_required(_source(**_ibm_info()))

	assert driver._target() == _ibm_target("t1")


def test_qrmi_ibm_checks_a_marked_circuit_instead_of_compiling_it(monkeypatch):
	import util.circuit_payload as circuit_payload
	import util.ibm_transcode as ibm_transcode
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	checked = []
	monkeypatch.setattr(ibm_transcode, "compile_circuit", _no_compile)
	monkeypatch.setattr(
		ibm_transcode, "require_isa_circuit",
		lambda circuit, target: checked.append((circuit, target)) or circuit)
	monkeypatch.setattr(
		ibm_transcode, "qiskit_sampler_input_json",
		lambda circuit, values, shots: "input-json")
	monkeypatch.setattr(circuit_payload, "qiskit_input", lambda info: "isa")
	driver = QrmiDriver({"provider": "ibm"})
	driver._target = lambda credential=None: {
		"properties": {"last_update_date": "t1"}}
	driver._resource = lambda: types.SimpleNamespace(
		Payload=types.SimpleNamespace(QiskitPrimitive=dict))
	marker = isa.make_marker("ibm", "ibm_torino", calibration={
		"kind": "properties-time", "value": "t1"})

	payload = driver._build_ibm_payload(_source(isa=marker), "target")

	assert checked == [("isa", "target")]
	assert payload == {"input": "input-json", "program_id": "sampler"}


def test_qrmi_ibm_refuses_labels_that_are_not_its_indices(monkeypatch):
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	from util import circuit_payload
	monkeypatch.setattr(circuit_payload, "qiskit_input", lambda info: "marked")
	driver = QrmiDriver({"provider": "ibm"})
	marker = isa.make_marker("ibm", "ibm_torino", qubit_labels=[1, 0])

	with pytest.raises(DEFwExecutionError, match="by index"):
		driver._build_ibm_payload(_source(isa=marker), "target")


def test_qrmi_ibm_still_compiles_a_generic_circuit(monkeypatch):
	import util.circuit_payload as circuit_payload
	import util.ibm_transcode as ibm_transcode
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	compiled = []
	monkeypatch.setattr(ibm_transcode, "require_isa_circuit", _no_compile)
	monkeypatch.setattr(
		ibm_transcode, "compile_circuit",
		lambda circuit, target, options: compiled.append(circuit) or circuit)
	monkeypatch.setattr(
		ibm_transcode, "qiskit_sampler_input_json",
		lambda circuit, values, shots: "input-json")
	monkeypatch.setattr(circuit_payload, "qiskit_input", lambda info: "generic")
	driver = QrmiDriver({"provider": "ibm"})
	driver._resource = lambda: types.SimpleNamespace(
		Payload=types.SimpleNamespace(QiskitPrimitive=dict))

	driver._build_ibm_payload(_source(), "target")

	assert compiled == ["generic"]


class _Unsupported(Exception):
	pass


def _ibm_qdmi_encode(monkeypatch, info, preprocess, descriptor=None):
	import qiskit
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers.qdmi_profiles import IbmQdmiProfile
	exceptions = types.ModuleType("mqt.core.plugins.qiskit.exceptions")
	exceptions.UnsupportedOperationError = _Unsupported
	qdmi = types.ModuleType("mqt.core.qdmi")
	qdmi.ProgramFormat = types.SimpleNamespace(QASM3="fmt")
	monkeypatch.setitem(sys.modules, exceptions.__name__, exceptions)
	monkeypatch.setitem(sys.modules, qdmi.__name__, qdmi)
	monkeypatch.setattr(qiskit, "transpile", _no_compile, raising=False)
	circuit = types.SimpleNamespace(parameters=[], cregs=[], clbits=[])
	monkeypatch.setattr(
		iqm_transcode, "load_qiskit_circuit", lambda source: circuit)
	backend = types.SimpleNamespace(_preprocess_circuit=preprocess)
	driver = types.SimpleNamespace(_descriptor=descriptor or {})
	profile = IbmQdmiProfile.__new__(IbmQdmiProfile)
	return profile.encode(driver, "source", info, backend)


def test_ibm_qdmi_does_not_fall_back_to_transpiling_a_marked_circuit(
		monkeypatch):
	def preprocess(circuit):
		raise _Unsupported("not native")
	marker = isa.make_marker("ibm", "ibm_torino")

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		_ibm_qdmi_encode(monkeypatch, {"isa": marker}, preprocess)


def test_ibm_qdmi_refuses_labels_that_are_not_its_indices(monkeypatch):
	marker = isa.make_marker("ibm", "ibm_torino", qubit_labels=[1, 0])

	with pytest.raises(DEFwExecutionError, match="by index"):
		_ibm_qdmi_encode(monkeypatch, {"isa": marker}, _no_compile)


def test_ibm_qdmi_refuses_when_the_device_requires_a_calibration_it_cannot_read(
		monkeypatch):
	marker = isa.make_marker("ibm", "ibm_torino", calibration={
		"kind": "properties-time", "value": "t1"})

	with pytest.raises(DEFwExecutionError, match="QDMI reports no calibration"):
		_ibm_qdmi_encode(
			monkeypatch, {"isa": marker}, _no_compile,
			descriptor={"calibration_policy": isa.POLICY_REQUIRE})


def test_ibm_qdmi_reports_no_mismatch_for_a_calibration_it_cannot_read(
		monkeypatch, caplog):
	marker = isa.make_marker("ibm", "ibm_torino", calibration={
		"kind": "properties-time", "value": "t1"})

	class Preprocessed(Exception):
		pass

	def preprocess(circuit):
		raise Preprocessed

	with caplog.at_level(logging.WARNING), pytest.raises(Preprocessed):
		_ibm_qdmi_encode(
			monkeypatch, {"isa": marker}, preprocess,
			descriptor={"calibration_policy": isa.POLICY_CURRENT})

	assert "current calibration" not in caplog.text


# --- the client --------------------------------------------------------------

def _job(monkeypatch, properties, marker=None, **options):
	import qfw_qiskit.qfw_job as qfw_job
	from qfw_qiskit.qfw_metadata import set_qubit_mapping

	monkeypatch.setattr(
		qfw_job, "encode_qiskit_circuit",
		lambda circuit, declared: properties.get("_encoded") or {
			"circuit": {"format": "qpy", "data": "AAAA"}})
	fake_qpm = FakeQPM(cids=["cid-isa"])
	fake_qpm.qpm_properties = properties
	circuit = qfw_job.QuantumCircuit(2, name="compiled")
	set_qubit_mapping(circuit, ["QB9", "QB8"])
	if marker is not None:
		metadata = dict(circuit.metadata)
		metadata["qfw"] = dict(metadata["qfw"], isa=marker)
		circuit.metadata = metadata
	backend = types.SimpleNamespace(returns_statevector=lambda: False)
	options = dict(FakeSlurmDriver().execution_options(shots=4), **options)
	job = qfw_job.QFwJob(backend, fake_qpm, FakeEventAPI(), circuit, options)
	return job, fake_qpm


def _isa_device(monkeypatch, properties):
	from tests.mock.test_qfw_backend_target import _backend
	return _backend(monkeypatch, qpm_properties=properties).isa_device()


def test_a_backend_names_the_device_its_qpm_compares(monkeypatch):
	assert _isa_device(monkeypatch, {
		"isa_provider": "iqm", "isa_device_id": DEVICE}) == ("iqm", DEVICE)


def test_a_backend_says_when_its_qpm_names_no_device(monkeypatch):
	from defw_exception import DEFwError
	with pytest.raises(DEFwError, match="compiled ahead of time"):
		_isa_device(monkeypatch, {"circuit_stages": [isa.STAGE_GENERIC]})


def test_the_client_lifts_the_marker_into_info(monkeypatch):
	job, fake_qpm = _job(
		monkeypatch, {"circuit_stages": STAGES}, marker=_marker())

	job.submit()

	payload = fake_qpm.submitted_payloads[0]
	assert payload["isa"] == _marker()
	assert payload["qubit_mapping"] == {"0": "QB1", "1": "QB2"}


def test_the_client_sends_no_marker_for_a_generic_circuit(monkeypatch):
	job, fake_qpm = _job(monkeypatch, {"circuit_stages": STAGES})

	job.submit()

	assert "isa" not in fake_qpm.submitted_payloads[0]


@pytest.mark.parametrize("properties", [
	{},
	{"circuit_stages": [isa.STAGE_GENERIC]},
])
def test_the_client_refuses_a_qpm_that_does_not_take_isa(
		monkeypatch, properties):
	import qfw_qiskit.qfw_job as qfw_job
	job, fake_qpm = _job(monkeypatch, properties, marker=_marker())

	with pytest.raises(qfw_job.DEFwError, match="compiled ahead of time"):
		job.submit()

	assert fake_qpm.submitted_payloads == []


def _binding_job(monkeypatch, marker=None):
	import qfw_qiskit.qfw_job as qfw_job
	job, fake_qpm = _job(
		monkeypatch, {"circuit_stages": STAGES}, marker=marker,
		param_values={"theta": 0.5})
	bound = []
	monkeypatch.setattr(
		qfw_job, "bind_marked_circuit",
		lambda circuit, values: bound.append(values) or circuit)
	return job, fake_qpm, bound


def test_the_client_binds_a_marked_circuit_instead_of_sending_values(
		monkeypatch):
	job, fake_qpm, bound = _binding_job(monkeypatch, marker=_marker())

	job.submit()

	assert bound == [{"theta": 0.5}]
	assert "param_values" not in fake_qpm.submitted_payloads[0]


def test_the_client_sends_a_generic_circuits_values_alongside_it(monkeypatch):
	job, fake_qpm, bound = _binding_job(monkeypatch)

	job.submit()

	assert bound == []
	assert fake_qpm.submitted_payloads[0]["param_values"] == {"theta": 0.5}


def _result_metadata(monkeypatch, marker, qhw_result):
	import qfw_qiskit.qfw_job as qfw_job
	from tests.mock.fakes import make_result_event
	from tests.mock.test_qfw_job import FakeBackend
	job, _ = _job(
		monkeypatch, {"circuit_stages": STAGES}, marker=marker,
		seed_simulator=1)
	job._backend = FakeBackend()
	job._qpm_event_api = FakeEventAPI(events=[make_result_event(
		"cid-isa", result={"counts": {"00": 1}, "qhw_result": qhw_result})],
		fd=42)
	monkeypatch.setattr(
		qfw_job.select, "select", lambda r, w, x, t: (r, [], []))
	job.submit()
	return job.result().data["results"][0]["data"]["metadata"]


def test_a_marked_circuits_result_names_both_calibrations(monkeypatch):
	metadata = _result_metadata(
		monkeypatch, _marker(),
		{"schema": "qhw-result-v1", "calibration": {"id": "cal-2"}})

	assert metadata["calibration"] == {"compiled": "cal-1", "executed": "cal-2"}


def test_a_generic_circuits_result_names_no_calibration(monkeypatch):
	metadata = _result_metadata(
		monkeypatch, None,
		{"schema": "qhw-result-v1", "calibration": {"id": "cal-2"}})

	assert "calibration" not in metadata


def test_the_client_submits_none_of_a_batch_it_would_refuse_part_of(
		monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job
	job, fake_qpm = _job(monkeypatch, {}, marker=_marker())
	job._qobj = [qfw_job.QuantumCircuit(2, name="generic"), job._qobj]

	with pytest.raises(qfw_job.DEFwError, match="compiled ahead of time"):
		job.submit()

	assert fake_qpm.submitted_payloads == []


def test_the_client_can_send_a_marked_circuit_as_generic(monkeypatch, caplog):
	job, fake_qpm = _job(
		monkeypatch, {}, marker=_marker(), isa_policy="generic")

	with caplog.at_level(logging.WARNING):
		job.submit()

	assert "isa" not in fake_qpm.submitted_payloads[0]
	assert "generic circuit" in caplog.text


def test_the_client_refuses_an_isa_policy_it_does_not_know(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job
	job, _ = _job(
		monkeypatch, {"circuit_stages": STAGES}, marker=_marker(),
		isa_policy="ignore")

	with pytest.raises(qfw_job.DEFwError, match="isa_policy"):
		job.submit()


def test_the_client_refuses_to_send_a_marked_circuit_as_openqasm(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job
	job, fake_qpm = _job(monkeypatch, {
		"circuit_stages": STAGES,
		"_encoded": {"qasm": "OPENQASM 2.0;"},
	}, marker=_marker())

	with pytest.raises(qfw_job.DEFwError, match="QPY"):
		job.submit()

	assert fake_qpm.submitted_payloads == []


def test_mark_isa_keeps_the_circuits_other_metadata():
	from qfw_qiskit.qfw_metadata import (
		get_isa_marker, get_qubit_mapping, mark_isa, set_qubit_mapping)
	circuit = types.SimpleNamespace(metadata={"mine": 1})
	set_qubit_mapping(circuit, ["QB1"])

	mark_isa(circuit, "iqm", DEVICE, calibration=CALIBRATION)

	assert circuit.metadata["mine"] == 1
	assert get_qubit_mapping(circuit) == {"0": "QB1"}
	assert get_isa_marker(circuit)["device_id"] == DEVICE
