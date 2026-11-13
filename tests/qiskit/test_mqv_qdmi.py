# QFw's MQV profile with released MQT Core, without MQV's device package.
# MQT Core's own simulator device stands in for MQV's: both are QDMI devices
# with no Qiskit backend of their own, so the profile opens either the same
# way, compiles to its target and submits OpenQASM to it.

import pathlib
import sys
import time
import types

import pytest
from qiskit import QuantumCircuit, qasm2
from qiskit.circuit import Parameter

qdmi = pytest.importorskip("mqt.core.qdmi")
pytest.importorskip("mqt.core.plugins.qiskit")
from defw_exception import DEFwExecutionError  # noqa: E402

SERVICES = pathlib.Path(__file__).resolve().parents[2] / "services"
STAND_IN = "mqt.ddsim.default"


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
	from svc_lib_qpm.drivers.qdmi_profiles import MqvQdmiProfile
	return MqvQdmiProfile({"provider": "mqv", "id": "mqv-test", "qdmi_device_id": STAND_IN})


@pytest.fixture
def backend(profile):
	if STAND_IN not in qdmi.builtin_driver.registered_device_ids():
		pytest.skip(f"MQT Core registers no {STAND_IN}")
	return profile.open_backend({"qdmi_device_id": STAND_IN, "base_url": None})


def _run(backend, program, program_format, shots):
	job = backend.device.submit_job(
		program, getattr(qdmi.ProgramFormat, program_format), shots)
	deadline = time.monotonic() + 30
	while str(job.check()).rsplit(".", 1)[-1].upper() != "DONE":
		assert time.monotonic() < deadline, f"job still {job.check()}"
		time.sleep(0.05)
	return job.get_counts()


def _flagged():
	# One qubit set, measured into a classical bit other than its own, so a
	# result keyed in the wrong order shows.
	circuit = QuantumCircuit(3, 3)
	circuit.x(0)
	circuit.h(1)
	circuit.cx(1, 2)
	circuit.measure(0, 2)
	circuit.measure(1, 0)
	circuit.measure(2, 1)
	return circuit


@pytest.mark.parametrize("source_format", ["qiskit", "qasm2"])
def test_a_circuit_runs_as_openqasm2_with_its_bits_in_place(profile, backend, source_format):
	source = _flagged()
	if source_format == "qasm2":
		source = qasm2.dumps(source)
	program, program_format, measurement = profile.encode(None, source, {}, backend)
	assert program_format == "QASM2"
	assert program.startswith("OPENQASM 2.0")
	counts = profile.result_counts(_run(backend, program, program_format, 200), measurement)
	assert sum(counts.values()) == 200
	assert set(counts) <= {"100", "111"}


def test_unbound_parameters_fail_before_submission(profile, backend):
	circuit = QuantumCircuit(1, 1)
	circuit.rx(Parameter("theta"), 0)
	circuit.measure(0, 0)
	with pytest.raises(DEFwExecutionError, match="bound parameters"):
		profile.encode(None, circuit, {}, backend)


def test_an_unregistered_device_names_the_package(profile):
	with pytest.raises(DEFwExecutionError, match="MQV's QDMI device package"):
		profile.open_backend({"qdmi_device_id": "mqv.not-installed"})
