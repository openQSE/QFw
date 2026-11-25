# QFw's IBM profile with the released adapter, without provider access.

import pathlib
import sys
import types

import pytest
from qiskit import ClassicalRegister, QuantumCircuit, QuantumRegister, qasm2
from qiskit.circuit import Clbit, Parameter
from mqt.core.plugins.qiskit.exceptions import CircuitValidationError

IBMBackend = pytest.importorskip("ibm.qdmi.qiskit").IBMBackend

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "services"

from defw_exception import DEFwExecutionError  # noqa: E402


@pytest.fixture
def profile(monkeypatch):
	# Load the profile without starting QPM services or importing DEFw.
	for name, path in (
			("svc_lib_qpm", SERVICES / "svc_lib_qpm"),
			("svc_lib_qpm.drivers", SERVICES / "svc_lib_qpm/drivers")):
		if name not in sys.modules:
			package = types.ModuleType(name)
			package.__path__ = [str(path)]
			monkeypatch.setitem(sys.modules, name, package)
	from svc_lib_qpm.drivers.qdmi_profiles import IbmQdmiProfile
	return IbmQdmiProfile({"provider": "ibm", "id": "ibm-test"})


@pytest.fixture
def backend():
	return IBMBackend(backend_name="fake_manila")


def test_native_circuits_keep_physical_indices_and_classical_destinations(profile, backend):
	readout = ClassicalRegister(2, "readout")
	flag = ClassicalRegister(1, "flag")
	circuit = QuantumCircuit(QuantumRegister(5, "physical"), readout, flag)
	circuit.x(4)
	circuit.cx(3, 4)
	circuit.measure(4, readout[1])
	circuit.measure(3, flag[0])

	program, program_format, measurement = profile.encode(None, circuit, {}, backend)

	assert program_format == "QASM3"
	assert "x $4;" in program
	assert "cx $3, $4;" in program
	assert "c[0] = measure $3;" in program
	assert "c[1] = measure $4;" in program
	assert measurement.result_width == 3
	assert profile.result_counts({"10": 7}, measurement) == {"010": 7}


@pytest.mark.parametrize("source_format", ["qiskit", "qasm2"])
def test_generic_circuits_compile_against_the_native_target(profile, backend, source_format):
	circuit = QuantumCircuit(2, 2)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.measure(range(2), range(2))
	source = qasm2.dumps(circuit) if source_format == "qasm2" else circuit

	program, program_format, measurement = profile.encode(None, source, {}, backend)

	assert program_format == "QASM3"
	assert "h $" not in program
	assert "sx $" in program
	assert "measure $" in program
	assert measurement.result_width == 2


def test_unbound_parameters_are_rejected(profile, backend):
	circuit = QuantumCircuit(1, 1)
	circuit.rz(Parameter("theta"), 0)
	circuit.measure(0, 0)
	with pytest.raises(DEFwExecutionError, match="bound"):
		profile.encode(None, circuit, {}, backend)


@pytest.mark.parametrize("layout", ["loose", "overlapping", "reordered"])
def test_invalid_classical_register_layouts_are_rejected(profile, backend, layout):
	circuit = QuantumCircuit(1)
	if layout == "loose":
		circuit.add_bits([Clbit()])
	else:
		bits = [Clbit(), Clbit()]
		circuit.add_bits(bits)
		circuit.add_register(ClassicalRegister(bits=bits[::-1] if layout == "reordered" else bits, name="c"))
		if layout == "overlapping":
			circuit.add_register(ClassicalRegister(bits=bits, name="alias"))
	circuit.measure(0, 0)
	with pytest.raises(DEFwExecutionError, match="register"):
		profile.encode(None, circuit, {}, backend)


@pytest.mark.parametrize("invalid, error, message", [
	("no-measurement", CircuitValidationError, "measurement"),
	("infinite", RuntimeError, "non-finite parameter"),
	("nan", RuntimeError, "non-finite parameter"),
])
def test_adapter_validation_rejects_invalid_circuits(profile, backend, invalid, error, message):
	circuit = QuantumCircuit(1, 1)
	if invalid == "no-measurement":
		circuit.x(0)
	else:
		circuit.rz(float("inf") if invalid == "infinite" else float("nan"), 0)
		circuit.measure(0, 0)
	with pytest.raises(error, match=message):
		profile.encode(None, circuit, {}, backend)
