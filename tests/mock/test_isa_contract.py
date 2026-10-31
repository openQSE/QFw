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
	# Both circuit indices would land on QB1, and the circuit would still
	# serialize.
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
	isa.check_calibration(_marker(calibration=None), "cal-2", isa.POLICY_REQUIRE)


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


# --- the QPMs ----------------------------------------------------------------

def _base_qpm():
	from util.qpm.util_qpm import UTIL_QPM
	return UTIL_QPM.__new__(UTIL_QPM)


def test_a_qpm_declares_generic_circuits_only_unless_it_says_otherwise():
	assert _base_qpm().circuit_stages() == [isa.STAGE_GENERIC]


def test_a_qpm_that_does_not_take_isa_refuses_it_before_registering_it():
	# __new__ leaves it no controller, so reaching registration would fail
	# with an AttributeError rather than this refusal.
	with pytest.raises(DEFwExecutionError, match="compiled ahead of time"):
		_base_qpm().create_circuit(_info())


def test_the_native_iqm_qpm_does_not_take_isa():
	# Its client tier transpiles first, so it would re-route the circuit.
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
	driver._target = lambda credential=None: {
		"dynamic_quantum_architecture": {"calibration_set_id": "cal-1"}}

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
	driver._target = lambda credential=None: {
		"dynamic_quantum_architecture": {"calibration_set_id": "cal-2"}}

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		driver._run_iqm_circuit(_source(**_info()))


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
	# Runs the IBM profile's encode with mqt.core's error and a circuit that
	# passes its register checks standing in for the real ones.
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
	# QDMI reports no calibration identity for an IBM device, so a device
	# that requires one has nothing to compare against.
	marker = isa.make_marker("ibm", "ibm_torino", calibration={
		"kind": "properties-time", "value": "t1"})

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		_ibm_qdmi_encode(
			monkeypatch, {"isa": marker}, _no_compile,
			descriptor={"calibration_policy": isa.POLICY_REQUIRE})


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
	# Left from before compiling, which is what a stale mapping looks like.
	set_qubit_mapping(circuit, ["QB9", "QB8"])
	if marker is not None:
		metadata = dict(circuit.metadata)
		metadata["qfw"] = dict(metadata["qfw"], isa=marker)
		circuit.metadata = metadata
	backend = types.SimpleNamespace(returns_statevector=lambda: False)
	options = dict(FakeSlurmDriver().execution_options(shots=4), **options)
	job = qfw_job.QFwJob(backend, fake_qpm, FakeEventAPI(), circuit, options)
	return job, fake_qpm


def test_the_client_lifts_the_marker_into_info(monkeypatch):
	job, fake_qpm = _job(
		monkeypatch, {"circuit_stages": STAGES}, marker=_marker())

	job.submit()

	payload = fake_qpm.submitted_payloads[0]
	assert payload["isa"] == _marker()
	# The marker's labels name the compiled circuit, whatever came before.
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
