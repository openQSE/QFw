# Compiling ahead of time for each provider, on real Qiskit and the real IQM
# adapter. tests/qiskit/test_isa_circuits.py covers the QPM's side.

import types

import pytest
from qiskit import QuantumCircuit
from qiskit.providers.fake_provider import GenericBackendV2

from defw_exception import DEFwExecutionError
from util import compile_profiles, isa


def _ghz():
	circuit = QuantumCircuit(3, 3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)
	circuit.measure([0, 1, 2], [0, 1, 2])
	return circuit


def _deneb_architecture():
	pytest.importorskip("iqm.qiskit_iqm")
	from iqm.qiskit_iqm.fake_backends import IQMFakeDeneb
	from util.iqm_transcode import to_jsonable
	return to_jsonable(IQMFakeDeneb().architecture)


def _garnet_architecture():
	pytest.importorskip("iqm.qiskit_iqm")
	from iqm.qiskit_iqm.fake_backends.fake_garnet import IQMFakeGarnet
	from util.iqm_transcode import to_jsonable
	return to_jsonable(IQMFakeGarnet().architecture)


def _marker(provider, labels):
	return isa.make_marker(provider, "device", qubit_labels=labels)


@pytest.mark.parametrize("provider, reason", [
	("aws", "Braket"), ("AWS", "Braket"), ("pasqal", "Pulser"),
	("backline", "backline"),
])
def test_a_device_that_is_not_compiled_ahead_of_time_says_why(provider, reason):
	with pytest.raises(DEFwExecutionError, match=reason):
		compile_profiles.compile_profile(provider)


@pytest.mark.parametrize("provider", [None, "", "nwqsim", "tnqvm"])
def test_a_simulator_has_no_profile(provider):
	assert compile_profiles.compile_profile(provider) is None


def test_ibm_compiles_to_the_target_and_passes_its_own_check():
	profile = compile_profiles.compile_profile("ibm")
	device = {"target": GenericBackendV2(3, seed=1).target, "backend_info": {}}

	compiled, labels = profile.compile(_ghz(), device, seed_transpiler=1)

	assert labels is None
	profile.validate(compiled, device, _marker("ibm", None))


def test_a_target_compile_takes_every_transpile_option():
	profile = compile_profiles.compile_profile("ibm")
	device = {"target": GenericBackendV2(3, seed=1).target, "backend_info": {}}
	passes = []

	profile.compile(
		_ghz(), device, seed_transpiler=1, num_processes=1,
		callback=lambda **kwargs: passes.append(kwargs["pass_"]))

	assert passes


def test_a_native_circuit_is_still_laid_out_as_asked():
	profile = compile_profiles.compile_profile("ibm")
	device = {"target": GenericBackendV2(3, seed=1).target, "backend_info": {}}
	native, _ = profile.compile(_ghz(), device, seed_transpiler=1)

	compiled, _ = profile.compile(
		native, device, initial_layout=[2, 1, 0], seed_transpiler=1)

	assert compiled.layout.initial_index_layout()[:3] == [2, 1, 0]


def test_a_target_profile_compiles_a_batch_in_one_transpile(monkeypatch):
	import qiskit
	profile = compile_profiles.compile_profile("ibm")
	device = {"target": GenericBackendV2(3, seed=1).target, "backend_info": {}}
	transpile = qiskit.transpile
	calls = []

	def counting(circuits, **options):
		calls.append(len(circuits))
		return transpile(circuits, **options)

	monkeypatch.setattr(qiskit, "transpile", counting)

	compiled = profile.compile_all(
		[_ghz(), _ghz()], device, [None, None], seed_transpiler=1)

	assert calls == [2]
	assert all(labels is None for _, labels in compiled)
	for circuit, _ in compiled:
		profile.validate(circuit, device, _marker("ibm", None))


@pytest.mark.parametrize("provider", ["ibm", "iqm"])
def test_a_compiled_circuit_lays_out_its_observables(provider):
	from qiskit.quantum_info import SparsePauliOp
	profile = compile_profiles.compile_profile(provider)
	device = ({"dynamic": _garnet_architecture()} if provider == "iqm" else
		{"target": GenericBackendV2(5, seed=1).target, "backend_info": {}})
	circuit = QuantumCircuit(3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)

	compiled, _ = profile.compile(circuit, device, seed_transpiler=1)
	observable = SparsePauliOp(["ZZI"]).apply_layout(compiled.copy().layout)

	assert observable.num_qubits == compiled.num_qubits


def test_ibm_refuses_a_circuit_that_is_not_native():
	profile = compile_profiles.compile_profile("ibm")
	device = {"target": GenericBackendV2(3, seed=1).target, "backend_info": {}}

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		profile.validate(_ghz(), device, _marker("ibm", None))


def test_ibm_refuses_labels_other_than_its_indices():
	profile = compile_profiles.compile_profile("ibm")
	device = {"target": GenericBackendV2(3, seed=1).target, "backend_info": {}}
	compiled, _ = profile.compile(_ghz(), device, seed_transpiler=1)

	with pytest.raises(DEFwExecutionError, match="by index"):
		profile.validate(compiled, device, _marker("ibm", ["QB1", "QB2", "QB3"]))


@pytest.mark.parametrize("backend_info, calibration", [
	({"backend_properties": {"last_update_date": "t1"}},
		{"kind": "properties-time", "value": "t1"}),
	({"calibration_set_id": "cal-1"}, {"kind": "set-id", "value": "cal-1"}),
	({}, None),
])
def test_a_target_device_names_its_calibration(backend_info, calibration):
	profile = compile_profiles.compile_profile("ibm")

	assert profile.calibration({"backend_info": backend_info}) == calibration


def test_iqm_compiles_a_star_device_through_its_resonator():
	profile = compile_profiles.compile_profile("iqm")
	device = {"dynamic": _deneb_architecture()}

	compiled, labels = profile.compile(_ghz(), device)

	assert labels[-1] in device["dynamic"]["computational_resonators"]
	assert len(labels) == compiled.num_qubits
	profile.validate(compiled, device, _marker("iqm", labels))


def test_iqm_compiles_a_crystal_device_onto_its_qubits():
	profile = compile_profiles.compile_profile("iqm")
	device = {"dynamic": _garnet_architecture()}

	compiled, labels = profile.compile(_ghz(), device, optimization_level=1)

	assert set(labels) <= set(device["dynamic"]["qubits"])
	profile.validate(compiled, device, _marker("iqm", labels))


def test_iqm_puts_a_circuit_on_the_qubits_its_mapping_names():
	profile = compile_profiles.compile_profile("iqm")
	device = {"dynamic": _garnet_architecture()}
	mapping = {"0": "QB2", "1": "QB1", "2": "QB4"}

	compiled, labels = profile.compile(_ghz(), device, mapping)

	assert labels == ["QB2", "QB1", "QB4"]
	profile.validate(compiled, device, _marker("iqm", labels))


def test_iqm_options_reach_the_transpiler(monkeypatch):
	from util import iqm_transcode
	profile = compile_profiles.compile_profile("iqm")
	seen = {}

	def transpile(circuit, backend, mapping, **options):
		seen.update(options)
		return QuantumCircuit(1), {0: "QB1"}

	monkeypatch.setattr(iqm_transcode, "transpile_for_iqm", transpile)
	monkeypatch.setattr(
		iqm_transcode, "architecture_backend", lambda dynamic: None)

	profile.compile(_ghz(), {"dynamic": {}}, optimization_level=1)

	assert seen == {"optimization_level": 1}


def test_iqm_refuses_a_circuit_that_is_not_native():
	profile = compile_profiles.compile_profile("iqm")
	device = {"dynamic": _garnet_architecture()}

	with pytest.raises(DEFwExecutionError, match="could not serialize"):
		profile.validate(_ghz(), device, _marker("iqm", device["dynamic"]["qubits"]))


def test_iqm_says_when_the_qpm_reports_no_architecture():
	profile = compile_profiles.compile_profile("iqm")
	telemetry = types.SimpleNamespace(get_dynamic_backend_info=lambda: {
		"metadata_supported": False, "dynamic_architecture": {}})

	with pytest.raises(DEFwExecutionError, match="dynamic architecture"):
		profile.read_device(types.SimpleNamespace(telemetry=telemetry))


def test_iqm_says_when_no_library_serves_the_architecture():
	profile = compile_profiles.compile_profile("iqm")

	def not_served():
		raise DEFwExecutionError("no wired library implements it")

	telemetry = types.SimpleNamespace(get_dynamic_backend_info=not_served)

	with pytest.raises(compile_profiles.DeviceNotDescribed, match="no wired"):
		profile.read_device(types.SimpleNamespace(telemetry=telemetry))


def test_a_qiskit_target_cannot_stand_for_a_star_device():
	profile = compile_profiles.compile_profile("iqm")

	with pytest.raises(DEFwExecutionError, match="star device"):
		profile.labels_for_target({"dynamic": _deneb_architecture()})


def test_an_iqm_device_names_its_calibration_set():
	profile = compile_profiles.compile_profile("iqm")
	device = {"dynamic": {"calibration_set_id": "cal-1"}}

	assert profile.calibration(device) == {"kind": "set-id", "value": "cal-1"}
