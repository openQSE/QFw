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
if str(SERVICES) not in sys.path:
	sys.path.insert(0, str(SERVICES))

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
	# Only native metadata is synthetic. Target construction and conversion
	# run through IBM QDMI 1.0 and MQT Core's released implementations.
	sites = [types.SimpleNamespace(index=lambda i=i: i) for i in range(5)]
	pairs = [(sites[a], sites[b]) for a, b in
		[(0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2), (3, 4), (4, 3)]]
	operations = []
	for name, arity, parameters in [
			("x", 1, 0), ("sx", 1, 0), ("rz", 1, 1),
			("cx", 2, 0), ("measure", 1, 0), ("reset", 1, 0)]:
		operations.append(types.SimpleNamespace(
			name=lambda n=name: n, qubits_num=lambda a=arity: a,
			parameters_num=lambda p=parameters: p, is_zoned=lambda: False,
			sites=lambda a=arity: sites if a == 1 else [site for pair in pairs for site in pair],
			site_pairs=lambda a=arity: pairs if a == 2 else None,
			duration=lambda **kwargs: None, fidelity=lambda **kwargs: None))
	device = types.SimpleNamespace(
		name=lambda: "ibm-offline", version=lambda: "1.0.0",
		qubits_num=lambda: 5, operations=lambda: operations, coupling_map=lambda: pairs)
	return IBMBackend(device=device)


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
	assert "qubit[5] q;" in program
	assert "x q[4];" in program
	assert "cx q[3], q[4];" in program
	assert "bit[2] c0;" in program
	assert "bit[1] c1;" in program
	assert "c0[1] = measure q[4];" in program
	assert "c1[0] = measure q[3];" in program
	assert measurement is None
	counts = {"110": 7}
	assert profile.result_counts(counts, measurement) is counts


@pytest.mark.parametrize("source_format", ["qiskit", "qasm2"])
def test_generic_circuits_compile_against_the_native_target(profile, backend, source_format):
	circuit = QuantumCircuit(2, 2)
	circuit.h(0)
	circuit.cx(0, 1)
	circuit.measure(range(2), range(2))
	source = qasm2.dumps(circuit) if source_format == "qasm2" else circuit

	program, program_format, measurement = profile.encode(None, source, {}, backend)

	assert program_format == "QASM3"
	assert "qubit[5] q;" in program
	assert "h q[" not in program
	assert "sx q[" in program
	assert "measure q[" in program
	assert measurement is None


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


@pytest.mark.parametrize("invalid", ["no-measurement", "infinite", "nan"])
def test_adapter_validation_rejects_invalid_circuits(profile, backend, invalid):
	circuit = QuantumCircuit(1, 1)
	if invalid == "no-measurement":
		circuit.x(0)
	else:
		circuit.rz(float("inf") if invalid == "infinite" else float("nan"), 0)
		circuit.measure(0, 0)
	with pytest.raises(CircuitValidationError):
		profile.encode(None, circuit, {}, backend)
