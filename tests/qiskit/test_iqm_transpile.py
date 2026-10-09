# IQM transpilation without a client.
#
# QFw reaches IQM two ways. The native IQM QPM holds an IQMClient, so it could
# always transpile. The QRMI driver holds no client, because QRMI owns the
# connection, and it passed none to build_iqm_circuit. So that path never
# transpiled, and the two tiers below transpilation take a circuit that is
# already native: the serializer accepts nothing else, and the manual
# translator knows only x, rx, ry, cz, barrier and measure. A plain h or cx
# therefore failed the whole chain, which is what a GHZ is made of.
#
# Transpiling needs the device's qubits and gate loci, not a session, and the
# QRMI path already has them from target(). These tests drive the real
# transcoder with no client at all.
#
# They need qiskit, iqm-client's Qiskit adapter and iqm.pulse. The CI mock job
# installs none of those, so these run in QFw's own venv.

import os
import pathlib
import sys
import types

import pytest
from qiskit import QuantumCircuit


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

if "defw_exception" not in sys.modules:
	try:
		import defw_exception  # noqa: F401
	except ImportError:
		# Outside a QFw install there is no DEFw. util.iqm_transcode only
		# needs its exception type, so stand in a minimal one.
		stub = types.ModuleType("defw_exception")

		class DEFwExecutionError(Exception):
			pass

		stub.DEFwExecutionError = DEFwExecutionError
		sys.modules["defw_exception"] = stub


CALIBRATION_SET_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _architecture(count=5):
	# Shaped like the dynamic_quantum_architecture QRMI's target() returns: a
	# linear chain with prx, cz and measure, and the gate loci the transpiler
	# routes against.
	qubits = [f"QB{index}" for index in range(1, count + 1)]
	single = [[qubit] for qubit in qubits]
	pairs = [[qubits[index], qubits[index + 1]]
		 for index in range(len(qubits) - 1)]
	return {
		"calibration_set_id": CALIBRATION_SET_ID,
		"qubits": qubits,
		"computational_resonators": [],
		"gates": {
			"prx": {
				"implementations": {
					"drag_gaussian": {"loci": single}},
				"default_implementation": "drag_gaussian",
				"override_default_implementation": {}},
			"cz": {
				"implementations": {"tgss": {"loci": pairs}},
				"default_implementation": "tgss",
				"override_default_implementation": {}},
			"measure": {
				"implementations": {"constant": {"loci": single}},
				"default_implementation": "constant",
				"override_default_implementation": {}},
		},
	}


def _ghz(qubits=3):
	circuit = QuantumCircuit(qubits, qubits, name="ghz")
	circuit.h(0)
	for target in range(1, qubits):
		circuit.cx(target - 1, target)
	circuit.measure(range(qubits), range(qubits))
	return circuit


def _iqm():
	pytest.importorskip("iqm.qiskit_iqm")
	pytest.importorskip("iqm.pulse")
	pytest.importorskip("iqm.iqm_client")


def _loci(iqm_circuit):
	return sorted({qubit for operation in iqm_circuit.instructions
		       for qubit in (operation.locus or ())})


def test_a_ghz_runs_through_the_clientless_path():
	# The case that failed: h and cx, no client, nothing pre-transpiled.
	_iqm()
	from util.iqm_transcode import build_iqm_circuit

	iqm_circuit = build_iqm_circuit(_ghz(), _architecture(), None)

	names = sorted({op.name for op in iqm_circuit.instructions})
	assert names == ["cz", "measure", "prx"], names
	assert iqm_circuit.metadata.get("qfw_transpiled_to_iqm") is True
	# Three measurements, one per qubit, so the counts can be rebuilt.
	measures = [op for op in iqm_circuit.instructions
		    if op.name == "measure"]
	assert len(measures) == 3


def test_the_serializer_alone_cannot_take_a_ghz():
	# Why the transpile tier is load-bearing rather than an optimisation.
	# Without it a GHZ reaches a tier that only accepts native operations.
	_iqm()
	from defw_exception import DEFwExecutionError
	from util.iqm_transcode import serialize_qiskit_to_iqm

	with pytest.raises(DEFwExecutionError) as excinfo:
		serialize_qiskit_to_iqm(_ghz(), _architecture(), None)
	assert "native operations" in str(excinfo.value)


def test_an_already_native_circuit_is_unchanged_in_shape():
	# The native gate set still goes through, so the new tier does not
	# disturb callers that were already passing native circuits.
	_iqm()
	from util.iqm_transcode import build_iqm_circuit
	import math

	circuit = QuantumCircuit(2, 2, name="native")
	circuit.r(math.pi / 2, math.pi / 2, 0)
	circuit.cz(0, 1)
	circuit.measure([0, 1], [0, 1])

	iqm_circuit = build_iqm_circuit(circuit, _architecture(), None)

	assert [op.name for op in iqm_circuit.instructions][:2] == ["prx", "cz"]


def test_a_mapping_puts_the_circuit_on_the_named_qubits():
	# The quiet one. A restricted transpile renumbers the circuit onto the
	# restricted set, so index 0 is the first MAPPED qubit and not the
	# device's first. Serializing with the device's full index map would
	# silently run the circuit on QB1..QB3 instead.
	_iqm()
	from util.iqm_transcode import build_iqm_circuit

	iqm_circuit = build_iqm_circuit(
		_ghz(), _architecture(), ["QB3", "QB4", "QB5"])

	assert _loci(iqm_circuit) == ["QB3", "QB4", "QB5"]
	# The metadata is JSON-ified on its way into the run request, so the
	# map's keys arrive as strings.
	assert iqm_circuit.metadata["logical_to_physical"] == {
		"0": "QB3", "1": "QB4", "2": "QB5"}


def test_a_mapping_given_as_a_dict_is_honoured_too():
	_iqm()
	from util.iqm_transcode import build_iqm_circuit

	iqm_circuit = build_iqm_circuit(
		_ghz(), _architecture(), {0: "QB2", 1: "QB3", 2: "QB4"})

	assert _loci(iqm_circuit) == ["QB2", "QB3", "QB4"]


def test_without_a_mapping_the_device_order_is_used():
	_iqm()
	from util.iqm_transcode import build_iqm_circuit

	iqm_circuit = build_iqm_circuit(_ghz(), _architecture(), None)

	# Routed onto the chain's first qubits, named by the device's own order.
	assert _loci(iqm_circuit) == ["QB1", "QB2", "QB3"]


def test_the_architecture_names_the_calibration_set():
	# A client-free backend has nothing to ask for the calibration set, but
	# the architecture it was built from carries one.
	_iqm()
	from util.iqm_transcode import build_iqm_circuit

	iqm_circuit = build_iqm_circuit(_ghz(), _architecture(), None)

	assert iqm_circuit.metadata.get(
		"iqm_calibration_set_id") == CALIBRATION_SET_ID


def test_an_architecture_without_gate_loci_falls_back():
	# What the QDMI profile passes when it cannot rebuild the architecture
	# (qdmi_profiles.iqm_architecture): the qubits alone, so there is nothing
	# to transpile against and the caller has to fall through to serializing
	# an already-native circuit.
	_iqm()
	from util.iqm_transcode import build_iqm_circuit
	import math

	circuit = QuantumCircuit(2, 2, name="native")
	circuit.r(math.pi / 2, math.pi / 2, 0)
	circuit.cz(0, 1)
	circuit.measure([0, 1], [0, 1])

	iqm_circuit = build_iqm_circuit(circuit, {"qubits": ["QB1", "QB2"]}, None)

	assert [op.name for op in iqm_circuit.instructions] == [
		"prx", "cz", "measure", "measure"]
	# The serializer ran, not the transpiler.
	assert "qfw_transpiled_to_iqm" not in (iqm_circuit.metadata or {})


def test_architecture_backend_refuses_to_submit():
	# It exists to carry the architecture through the transpiler. Submitting
	# is QRMI's job, and the abstract run() must not look like it worked.
	_iqm()
	from defw_exception import DEFwExecutionError
	from util.iqm_transcode import architecture_backend

	backend = architecture_backend(_architecture())
	assert backend.num_qubits == 5
	with pytest.raises(DEFwExecutionError):
		backend.run(None)


def _qdmi_iqm_architecture(monkeypatch):
	# qdmi_profiles.iqm_architecture, imported by path: the svc_lib_qpm
	# package's own __init__ boots the QPM service, which this does not need.
	if "svc_lib_qpm" not in sys.modules:
		package = types.ModuleType("svc_lib_qpm")
		package.__path__ = [os.path.join(SERVICES, "svc_lib_qpm")]
		monkeypatch.setitem(sys.modules, "svc_lib_qpm", package)
	from svc_lib_qpm.drivers.qdmi_profiles import iqm_architecture
	return iqm_architecture


def _fomac_topology(count=5):
	# The same chain as _architecture(), the way FoMaC reports it
	# (fomac_normalize.extract_topology): qubit labels and each operation's
	# loci, with no implementation names and no calibration set id.
	qubits = [f"QB{index}" for index in range(1, count + 1)]
	single = [[qubit] for qubit in qubits]
	return {
		"num_qubits": count,
		"qubits": qubits,
		"edges": [[qubits[index], qubits[index + 1]]
			  for index in range(count - 1)],
		"operations": {
			"prx": single,
			"cz": [[qubits[index], qubits[index + 1]]
			       for index in range(count - 1)],
			"measure": single,
		},
	}


def test_a_ghz_runs_through_the_architecture_qdmi_rebuilds(monkeypatch):
	# The QDMI path had only the qubit list, so h and cx failed there as they
	# did on QRMI. Rebuilt from FoMaC, the architecture transpiles the same.
	_iqm()
	from util.iqm_transcode import build_iqm_circuit
	iqm_architecture = _qdmi_iqm_architecture(monkeypatch)

	arch = iqm_architecture(_fomac_topology(), CALIBRATION_SET_ID)
	iqm_circuit = build_iqm_circuit(_ghz(), arch, None)

	names = sorted({op.name for op in iqm_circuit.instructions})
	assert names == ["cz", "measure", "prx"], names
	assert iqm_circuit.metadata.get("qfw_transpiled_to_iqm") is True
	assert iqm_circuit.metadata.get(
		"iqm_calibration_set_id") == CALIBRATION_SET_ID
	assert _loci(iqm_circuit) == ["QB1", "QB2", "QB3"]


def test_a_mapping_holds_on_the_architecture_qdmi_rebuilds(monkeypatch):
	_iqm()
	from util.iqm_transcode import build_iqm_circuit
	iqm_architecture = _qdmi_iqm_architecture(monkeypatch)

	arch = iqm_architecture(_fomac_topology(), CALIBRATION_SET_ID)
	iqm_circuit = build_iqm_circuit(_ghz(), arch, ["QB3", "QB4", "QB5"])

	assert _loci(iqm_circuit) == ["QB3", "QB4", "QB5"]


def _architecture_without_measure():
	# IQMBackendBase keeps only the qubits a measure gate covers, and reads
	# gates["measure"] to find them, so without one it raises a KeyError
	# while it builds its target.
	arch = _architecture()
	del arch["gates"]["measure"]
	return arch


def test_architecture_backend_reports_an_unusable_architecture():
	_iqm()
	from defw_exception import DEFwExecutionError
	from util.iqm_transcode import architecture_backend

	with pytest.raises(DEFwExecutionError) as excinfo:
		architecture_backend(_architecture_without_measure())
	assert "measure" in str(excinfo.value)


def test_a_backend_that_cannot_be_built_falls_back():
	# The KeyError used to escape build_iqm_circuit, which falls back only on
	# a DEFwExecutionError. The GHZ now reaches the manual translator and
	# gets its error, which names the gates it can take.
	_iqm()
	from defw_exception import DEFwExecutionError
	from util.iqm_transcode import build_iqm_circuit

	with pytest.raises(DEFwExecutionError) as excinfo:
		build_iqm_circuit(_ghz(), _architecture_without_measure(), None)
	assert "x, rx, ry, cz" in str(excinfo.value)


def test_a_client_that_cannot_build_a_backend_falls_back():
	# Building IQMBackend asks the client for the architecture. A failure
	# there falls back to serializing an already-native circuit, as it did
	# before the client-free path was added, rather than ending the run.
	_iqm()
	from util.iqm_transcode import build_iqm_circuit
	import math

	class UnreachableClient:
		def __getattr__(self, name):
			def call(*args, **kwargs):
				raise RuntimeError(f"{name}: server unreachable")
			return call

	circuit = QuantumCircuit(2, 2, name="native")
	circuit.r(math.pi / 2, math.pi / 2, 0)
	circuit.cz(0, 1)
	circuit.measure([0, 1], [0, 1])

	iqm_circuit = build_iqm_circuit(
		circuit, {"qubits": ["QB1", "QB2"]}, None,
		client=UnreachableClient())

	assert [op.name for op in iqm_circuit.instructions][:2] == ["prx", "cz"]
	assert "qfw_transpiled_to_iqm" not in (iqm_circuit.metadata or {})
