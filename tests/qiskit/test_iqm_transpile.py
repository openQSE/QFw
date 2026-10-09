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
	# What the QDMI profile passes: FoMaC reports the topology, not the gate
	# loci, so there is nothing to transpile against and the caller has to
	# fall through to serializing an already-native circuit.
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
