# The client half of the declared-format contract, with a real Qiskit: what
# the frontend serializes for a QPM that declares QPY, and what happens when
# it declares only OpenQASM 2. The QPM's own reader (util.circuit_payload)
# loads it back, so these are true round trips rather than encoder snapshots.
#
# The CI mock job has no qiskit, so it does not run these. The format choice
# itself is covered without qiskit in tests/mock/test_circuit_payload.py.

import pathlib
import sys
import types

import pytest
from qiskit import QuantumCircuit, qasm2, qpy
from qiskit.qpy import common


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

if "defw_exception" not in sys.modules:
	try:
		import defw_exception  # noqa: F401
	except ImportError:
		stub = types.ModuleType("defw_exception")

		class DEFwExecutionError(Exception):
			pass

		stub.DEFwExecutionError = DEFwExecutionError
		sys.modules["defw_exception"] = stub

from defw_exception import DEFwExecutionError  # noqa: E402
from util import circuit_payload  # noqa: E402


QPY_READER = {
	"circuit_formats": ["qpy", "openqasm2"],
	"qpy_version": qpy.QPY_VERSION,
}
QASM_READER = {"circuit_formats": ["openqasm2"]}


def _dynamic_circuit():
	# if_else is the plain case OpenQASM 2 cannot express at all.
	circuit = QuantumCircuit(2, 2, name="dynamic")
	circuit.h(0)
	circuit.measure(0, 0)
	with circuit.if_test((circuit.clbits[0], 1)):
		circuit.x(1)
	circuit.measure(1, 1)
	circuit.metadata = {"experiment": "declared-format"}
	return circuit


def test_openqasm2_really_cannot_carry_the_circuit_we_send_as_qpy():
	# The premise of the whole change. If this ever stops raising, the
	# fallback below is no longer a loss and this test should say so.
	with pytest.raises(Exception):
		qasm2.dumps(_dynamic_circuit())


def test_a_dynamic_circuit_reaches_a_qpy_reader_intact():
	circuit = _dynamic_circuit()
	fields = circuit_payload.encode_qiskit_circuit(circuit, QPY_READER)

	assert set(fields) == {"circuit"}
	assert fields["circuit"]["format"] == "qpy"

	# Read it back the way the QPM does.
	loaded = circuit_payload.qiskit_input(fields)
	assert loaded.name == "dynamic"
	assert loaded.metadata == {"experiment": "declared-format"}
	assert [instruction.name for instruction in loaded.data] == \
		[instruction.name for instruction in circuit.data]


def test_a_qpm_reading_an_older_qpy_gets_one_it_can_load():
	circuit = _dynamic_circuit()
	older = common.QPY_COMPATIBILITY_VERSION
	properties = {"circuit_formats": ["qpy"], "qpy_version": older}

	assert circuit_payload.choose_circuit_format(properties) == ("qpy", older)
	fields = circuit_payload.encode_qiskit_circuit(circuit, properties)
	loaded = circuit_payload.qiskit_input(fields)
	assert loaded.name == "dynamic"


def test_a_qpm_that_declares_only_openqasm2_still_gets_qasm():
	circuit = QuantumCircuit(2, name="plain")
	circuit.h(0)
	circuit.cx(0, 1)

	fields = circuit_payload.encode_qiskit_circuit(circuit, QASM_READER)
	assert set(fields) == {"qasm"}
	assert fields["qasm"].startswith("OPENQASM 2.0;")
	# And the QPM reads it through the same entry point.
	assert circuit_payload.circuit_payload(fields)[0] == "openqasm2"


def test_an_unsendable_circuit_says_what_the_qpm_reads():
	with pytest.raises(DEFwExecutionError) as excinfo:
		circuit_payload.encode_qiskit_circuit(_dynamic_circuit(), QASM_READER)
	message = str(excinfo.value)
	assert "this QPM reads openqasm2" in message
