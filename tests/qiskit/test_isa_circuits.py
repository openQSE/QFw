# A circuit compiled ahead of time, on real Qiskit and the real IQM adapter: it
# reaches the device as it was compiled, or it is refused. tests/mock covers the
# contract around it (test_isa_contract.py).

import dataclasses
import io
import pathlib
import sys

import pytest
from qiskit import QuantumCircuit, qpy
from qiskit.circuit import Parameter
from qiskit.circuit.library import CZGate, Measure, RZGate, SXGate, XGate
from qiskit.transpiler import Target

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in ("services", "backends"):
	if str(REPO_ROOT / path) not in sys.path:
		sys.path.insert(0, str(REPO_ROOT / path))

from defw_exception import DEFwExecutionError  # noqa: E402
from util import isa  # noqa: E402
from util.circuit_payload import (  # noqa: E402
	encode_qiskit_circuit, qiskit_input)
from util.ibm_transcode import require_isa_circuit  # noqa: E402


QUBITS = ["QB1", "QB2", "QB3"]


def _architecture():
	# A chain: QB1-QB2-QB3, so QB1 and QB3 are not coupled.
	single = [[qubit] for qubit in QUBITS]
	return {
		"calibration_set_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
		"qubits": list(QUBITS),
		"computational_resonators": [],
		"gates": {
			"prx": {
				"implementations": {"drag": {"loci": single}},
				"default_implementation": "drag",
				"override_default_implementation": {}},
			"cz": {
				"implementations": {"tgss": {"loci": [
					["QB1", "QB2"], ["QB2", "QB3"]]}},
				"default_implementation": "tgss",
				"override_default_implementation": {}},
			"measure": {
				"implementations": {"constant": {"loci": single}},
				"default_implementation": "constant",
				"override_default_implementation": {}},
		},
	}


def _marker(labels=QUBITS):
	return isa.make_marker("iqm", "q20", qubit_labels=labels)


def _native(first, second):
	circuit = QuantumCircuit(3, 2)
	circuit.r(1.0, 0.5, first)
	circuit.cz(first, second)
	circuit.measure([first, second], [0, 1])
	return circuit


def _iqm():
	pytest.importorskip("iqm.qiskit_iqm")
	pytest.importorskip("iqm.pulse")
	from util import iqm_transcode
	return iqm_transcode


def _loci(iqm_circuit, name):
	return [operation.locus for operation in iqm_circuit.instructions
		if operation.name == name]


def test_a_marked_circuit_keeps_the_qubits_it_was_compiled_onto():
	iqm_transcode = _iqm()

	built = iqm_transcode.build_isa_iqm_circuit(
		_native(1, 2), _architecture(), _marker())

	assert _loci(built, "cz") == [("QB2", "QB3")]
	assert _loci(built, "prx") == [("QB2",)]


def test_a_marked_circuit_names_its_qubits_by_label_not_by_device_order():
	# Device order says index 0 is QB1. The compiler said it is QB3.
	iqm_transcode = _iqm()
	labels = ["QB3", "QB2", "QB1"]

	built = iqm_transcode.build_isa_iqm_circuit(
		_native(0, 1), _architecture(), _marker(labels))

	assert _loci(built, "cz") == [("QB3", "QB2")]


def test_a_marked_circuit_is_not_compiled_to_fit():
	# A generic h would be transpiled. A marked one says it was compiled, so
	# it is wrong, and rewriting it would hide that.
	iqm_transcode = _iqm()
	circuit = QuantumCircuit(3, 1)
	circuit.h(0)
	circuit.measure(0, 0)

	with pytest.raises(DEFwExecutionError):
		iqm_transcode.build_isa_iqm_circuit(
			circuit, _architecture(), _marker())


def test_a_marked_circuit_on_uncoupled_qubits_is_refused():
	# The gate is native, so the serializer takes it. Only the architecture
	# says this pair has no coupler.
	iqm_transcode = _iqm()

	with pytest.raises(DEFwExecutionError, match="no cz"):
		iqm_transcode.build_isa_iqm_circuit(
			_native(0, 2), _architecture(), _marker())


def test_a_generic_circuit_on_uncoupled_qubits_still_serializes():
	# What the check adds: without a marker this is the path it always was.
	iqm_transcode = _iqm()

	built = iqm_transcode.build_iqm_circuit(
		_native(0, 2), _architecture(), None)

	assert _loci(built, "cz") == [("QB1", "QB3")]


def test_a_marked_circuit_on_a_qubit_the_device_lacks_is_refused():
	iqm_transcode = _iqm()
	labels = ["QB1", "QB2", "QB9"]

	with pytest.raises(DEFwExecutionError, match="QB9"):
		iqm_transcode.build_isa_iqm_circuit(
			_native(1, 2), _architecture(), _marker(labels))


def test_a_marked_iqm_circuit_needs_its_labels():
	iqm_transcode = _iqm()

	with pytest.raises(DEFwExecutionError, match="qubit_labels"):
		iqm_transcode.build_isa_iqm_circuit(
			_native(0, 1), _architecture(),
			isa.make_marker("iqm", "q20"))


def test_an_architecture_with_no_gates_cannot_vouch_for_a_circuit():
	iqm_transcode = _iqm()
	architecture = dict(_architecture(), gates={})

	with pytest.raises(DEFwExecutionError, match="names no gates"):
		iqm_transcode.build_isa_iqm_circuit(
			_native(0, 1), architecture, _marker())


def _star_circuit():
	# Compiled the way a client does, resonator and all.
	_iqm()
	from iqm.qiskit_iqm import transpile_to_IQM
	from iqm.qiskit_iqm.fake_backends import IQMFakeDeneb

	backend = IQMFakeDeneb()
	circuit = QuantumCircuit(3, 3)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.cx(1, 2)
	circuit.measure(range(3), range(3))
	compiled = transpile_to_IQM(circuit, backend)
	labels = [backend.index_to_qubit_name(index)
		  for index in range(compiled.num_qubits)]
	return compiled, labels, backend.architecture


def test_a_circuit_compiled_for_a_star_device_runs_through_its_resonator():
	iqm_transcode = _iqm()
	compiled, labels, architecture = _star_circuit()

	built = iqm_transcode.build_isa_iqm_circuit(
		compiled, architecture, _marker(labels))

	assert "CR1" in labels
	assert any("CR1" in locus for locus in _loci(built, "move"))


def test_a_move_is_directed():
	# A cz is the same either way round. A move is not.
	iqm_transcode = _iqm()
	compiled, labels, architecture = _star_circuit()
	built = iqm_transcode.build_isa_iqm_circuit(
		compiled, architecture, _marker(labels))
	operation = next(
		item for item in built.instructions if item.name == "move")
	reverse = dataclasses.replace(built, instructions=(
		dataclasses.replace(operation, locus=operation.locus[::-1]),))

	with pytest.raises(DEFwExecutionError, match="no move"):
		iqm_transcode.check_iqm_loci(reverse, architecture)


def test_the_marker_survives_the_trip_to_the_qpm():
	# A compile job writes a QPY file, a run job reads it, and the client
	# encodes it for the QPM. The circuit the QPM loads is the compiled one.
	circuit = _native(1, 2)
	circuit.metadata = {"qfw": {"isa": _marker()}}
	handed_over = io.BytesIO()
	qpy.dump(circuit, handed_over)
	handed_over.seek(0)
	loaded = qpy.load(handed_over)[0]
	assert loaded.metadata["qfw"]["isa"] == _marker()

	info = encode_qiskit_circuit(loaded, {
		"circuit_formats": ["qpy+gzip", "qpy"],
		"qpy_version": qpy.QPY_VERSION,
	})
	received = qiskit_input(info)

	assert received == circuit
	assert isa.check_envelope(
		dict(info, isa=_marker()), ["generic", "isa"], "q20") == _marker()


def _ibm_target():
	target = Target(num_qubits=3)
	target.add_instruction(
		RZGate(Parameter("theta")), {(index,): None for index in range(3)})
	target.add_instruction(SXGate(), {(index,): None for index in range(3)})
	target.add_instruction(XGate(), {(index,): None for index in range(3)})
	target.add_instruction(CZGate(), {(0, 1): None, (1, 2): None})
	target.add_instruction(Measure(), {(index,): None for index in range(3)})
	return target


def test_an_ibm_circuit_native_to_the_device_passes():
	circuit = QuantumCircuit(3, 1)
	circuit.sx(0)
	circuit.cz(0, 1)
	circuit.measure(1, 0)

	assert require_isa_circuit(circuit, _ibm_target()) is circuit


def test_an_ibm_circuit_on_uncoupled_qubits_is_refused():
	circuit = QuantumCircuit(3)
	circuit.cz(0, 2)

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		require_isa_circuit(circuit, _ibm_target())


def test_an_ibm_circuit_with_a_foreign_gate_is_refused():
	circuit = QuantumCircuit(3)
	circuit.h(0)

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		require_isa_circuit(circuit, _ibm_target())
