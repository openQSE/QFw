import pathlib
import sys
import importlib.util

import pytest
from qiskit import QuantumCircuit, transpile


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET_MODULE = REPO_ROOT / "backends" / "qfw_qiskit" / "qfw_target.py"


spec = importlib.util.spec_from_file_location("qfw_target", TARGET_MODULE)
qfw_target = importlib.util.module_from_spec(spec)
sys.modules["qfw_target"] = qfw_target
spec.loader.exec_module(qfw_target)


def test_qfw_target_transpiles_pennylane_remote_circuit_shape():
	target = qfw_target.build_qfw_target(num_qubits=400)
	circuit = QuantumCircuit(2)
	circuit.rz(0.3, 0)
	circuit.ry(0.1, 0)
	circuit.rx(0.2, 0)
	circuit.cx(0, 1)

	transpiled = transpile(circuit, target=target)

	assert transpiled.num_qubits == 2


def _iqm_records():
	# A four-qubit IQM-shaped device in the shapes the shim's telemetry
	# returns (see tests/mock/test_qfw_backend_target.py for the full set).
	qubits = ["QB1", "QB2", "QB3", "QB4"]
	edges = [["QB1", "QB2"], ["QB2", "QB3"], ["QB3", "QB4"]]
	single = [[qubit] for qubit in qubits]
	device = {
		"schema": "qhw-device-v1", "provider": "iqm",
		"device": {"id": "ornl-4q", "num_qubits": 4},
		"qubits": [{"id": q, "index": i} for i, q in enumerate(qubits)],
	}
	info = {
		"backend": "iqm", "metadata_supported": True,
		"static_architecture": {"qubits": qubits, "connectivity": edges},
		"active_qubits": qubits, "calibration_set_id": "cal-1",
		"qhw_device": device,
	}
	coupling = {
		"schema": "qhw-coupling-v1", "provider": "iqm",
		"device": {"id": "ornl-4q"},
		"coupling": {"directed": False, "nodes": qubits, "edges": edges},
		"operations": [
			{"name": "cz", "arity": 2,
			 "supported_loci": [["QB2", "QB1"], ["QB2", "QB3"], ["QB4", "QB3"]]},
			{"name": "measure", "arity": 1, "supported_loci": single},
			{"name": "prx", "arity": 1, "supported_loci": single},
			{"name": "cc_prx", "arity": 1, "supported_loci": single},
		],
	}
	calibration = {
		"schema": "qhw-calibration-v1", "provider": "iqm",
		"device": {"id": "ornl-4q"},
		"calibration": {"calibration_set_id": "cal-1"},
		"extensions": {"iqm.v1": {
			"quality_metric_set": {"observations": [
				{"dut_field": "metrics.irb.cz.crf_crf.QB1__QB2.fidelity:par=d2",
				 "value": 0.99, "unit": "", "invalid": False},
				{"dut_field": "characterization.model.QB1.t1_time",
				 "value": 50e-6, "unit": "s", "invalid": False},
			]},
			"calibration_set": {"observations": [
				{"dut_field": "gates.cz.crf_crf.QB2__QB1.duration",
				 "value": 6e-08, "unit": "", "invalid": False},
			]},
		}},
	}
	return info, device, coupling, calibration


def test_device_target_from_qhw_records_transpiles_to_the_native_gates():
	info, device, coupling, calibration = _iqm_records()

	target = qfw_target.build_device_target(info, device, coupling, calibration)

	assert target.num_qubits == 4
	assert set(target.operation_names) == {"r", "cz", "measure"}
	assert set(target.qargs_for_operation_name("cz")) == {
		(1, 0), (0, 1), (1, 2), (2, 1), (3, 2), (2, 3)}
	assert target["cz"][(0, 1)].error == pytest.approx(0.01)
	assert target["cz"][(0, 1)].duration == pytest.approx(6e-08)
	assert target.qubit_properties[0].t1 == pytest.approx(50e-6)
	assert target.qubit_properties[1].t1 is None

	circuit = QuantumCircuit(3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)
	circuit.measure_all()
	transpiled = transpile(circuit, target=target, seed_transpiler=7)

	assert set(transpiled.count_ops()) <= {"r", "cz", "measure", "barrier"}
	assert transpiled.count_ops()["cz"] == 2
	layout = transpiled.layout.initial_index_layout(filter_ancillas=True)
	assert len(set(layout)) == 3


def test_device_target_from_an_ibm_configuration_uses_the_runtime_converter():
	pytest.importorskip("qiskit_ibm_runtime")
	import json
	from qiskit_ibm_runtime.fake_provider import FakeManilaV2

	fake = FakeManilaV2()
	# Through JSON, as the record arrives from the QPM.
	configuration = json.loads(json.dumps(
		fake.configuration().to_dict(), default=str))
	properties = json.loads(json.dumps(
		fake.properties().to_dict(), default=str))
	info = {
		"backend": "ibm", "metadata_supported": True,
		"static_architecture": configuration,
		"backend_properties": properties,
		"qhw_device": {},
	}

	target = qfw_target.build_device_target(info)

	assert target.num_qubits == 5
	assert {"cx", "rz", "sx", "x", "measure"} <= set(target.operation_names)
	assert target["cx"][(0, 1)].error is not None
	assert target["cx"][(0, 1)].duration is not None
	assert target.description == "QFw ibm fake_manila target"
	assert sorted(target.build_coupling_map().get_edges()) == sorted(
		tuple(edge) for edge in configuration["coupling_map"])

	# The same configuration without the converter: the connectivity and
	# the basis gates, but no calibration.
	bare = qfw_target.target_from_description(
		qfw_target.describe_ibm_configuration(configuration))
	assert bare.num_qubits == 5
	assert set(bare.operation_names) == {
		"cx", "id", "rz", "sx", "x", "measure", "reset"}
	assert bare["cx"][(0, 1)] is None


def test_generic_target_stands_in_for_a_qpm_without_metadata():
	assert qfw_target.build_device_target(
		{"backend": "nwqsim", "metadata_supported": False}) is None
	generic = qfw_target.build_qfw_target()
	assert generic.num_qubits == 400
	assert len(generic.operation_names) == 39


def test_a_defined_gate_is_read_as_the_instructions_its_body_uses():
	from qiskit.circuit.library import RYYGate, RXXGate

	assert qfw_target.openqasm2_instructions(RXXGate(0.1)) == ["rxx"]
	assert qfw_target.openqasm2_instructions(RYYGate(0.1)) == [
		"cx", "rz", "sx", "sxdg"]


def test_the_generic_target_keeps_only_gates_written_with_instructions_the_qpm_runs():
	every = {instruction.name for instruction in qfw_target.qfw_instructions()}
	runs = sorted(every - {"sxdg"})
	generic = qfw_target.build_qfw_target(num_qubits=4, instructions=runs)
	assert every - set(generic.operation_names) == {"sxdg", "ryy"}
	assert set(qfw_target.build_qfw_target(num_qubits=4).operation_names) == every
