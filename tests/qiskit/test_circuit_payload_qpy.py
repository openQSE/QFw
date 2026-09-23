# Real QPY round trips through util.circuit_payload and the IQM transcoder.
# They need qiskit, and iqm-client's Qiskit adapter for the transcoder test.
# The CI mock job installs neither, so these run in QFw's own venv.

import base64
import io
import math
import pathlib
import sys
import types

import pytest
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister
from qiskit import qasm2, qpy


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

if "defw_exception" not in sys.modules:
	try:
		import defw_exception  # noqa: F401
	except ImportError:
		# Outside a QFw install there is no DEFw. util.circuit_payload only
		# needs its exception type, so stand in a minimal one.
		stub = types.ModuleType("defw_exception")

		class DEFwExecutionError(Exception):
			pass

		stub.DEFwExecutionError = DEFwExecutionError
		sys.modules["defw_exception"] = stub

from util import circuit_payload  # noqa: E402


def _envelope(circuit, **dump_options):
	buffer = io.BytesIO()
	qpy.dump(circuit, buffer, **dump_options)
	return {"circuit": {
		"format": "qpy",
		"data": base64.b64encode(buffer.getvalue()).decode("ascii"),
	}}


def test_a_dynamic_circuit_survives_the_envelope():
	# The kind of circuit OpenQASM 2 cannot carry at all.
	qubits = QuantumRegister(2, "q")
	bits = ClassicalRegister(2, "c")
	circuit = QuantumCircuit(
		qubits, bits, name="feedback", metadata={"experiment": "x"})
	circuit.h(0)
	circuit.measure(0, 0)
	with circuit.if_test((bits[0], 1)):
		circuit.x(1)
	circuit.measure(1, 1)

	loaded = circuit_payload.qiskit_input(_envelope(circuit))

	assert loaded == circuit
	assert loaded.name == "feedback"
	assert loaded.metadata == {"experiment": "x"}


def test_the_oldest_qpy_this_qiskit_writes_still_loads():
	circuit = QuantumCircuit(1, 1)
	circuit.x(0)
	circuit.measure(0, 0)

	loaded = circuit_payload.qiskit_input(
		_envelope(circuit, version=qpy.QPY_COMPATIBILITY_VERSION))

	assert loaded == circuit


def test_the_declared_qpy_version_is_the_one_this_qiskit_reads():
	assert circuit_payload.qiskit_circuit_formats() == {
		"circuit_formats": ["qpy", "openqasm2"],
		"qpy_version": qpy.QPY_VERSION,
	}


def test_the_iqm_transcoder_takes_a_qpy_circuit_and_keeps_its_name():
	pytest.importorskip("iqm.qiskit_iqm")
	pytest.importorskip("iqm.pulse")
	from util.iqm_transcode import build_iqm_circuit
	circuit = QuantumCircuit(2, 2, name="bell")
	circuit.r(math.pi / 2, math.pi / 2, 0)
	circuit.cz(0, 1)
	circuit.measure([0, 1], [0, 1])
	architecture = {"qubits": ["QB1", "QB2"]}

	from_qpy = build_iqm_circuit(
		circuit_payload.qiskit_input(_envelope(circuit)), architecture, None)
	from_qasm = build_iqm_circuit(
		circuit_payload.qiskit_input({"qasm": qasm2.dumps(circuit)}),
		architecture, None)

	assert from_qpy.instructions == from_qasm.instructions
	assert [op.name for op in from_qpy.instructions] == [
		"prx", "cz", "measure", "measure"]
	# OpenQASM 2 has no circuit name, so only QPY keeps the one IQM sees.
	assert from_qpy.name == "bell"
	assert from_qasm.name != "bell"


def test_manual_translation_rejects_what_openqasm2_cannot_hold():
	from util.iqm_transcode import openqasm2_for_manual_translation
	bits = ClassicalRegister(1, "c")
	circuit = QuantumCircuit(QuantumRegister(1, "q"), bits)
	circuit.measure(0, 0)
	with circuit.if_test((bits[0], 1)):
		circuit.x(0)

	assert openqasm2_for_manual_translation("OPENQASM 2.0;") == "OPENQASM 2.0;"
	with pytest.raises(Exception, match="manual translation"):
		openqasm2_for_manual_translation(circuit)
