from importlib import metadata

import pytest

from qfw_qiskit.qhw_target import (
	SUPPORTED_QISKIT_VERSIONS,
	qhw_to_qiskit_target,
)
from tests.qhw_target_records import representative_qhw_records


def records():
	return representative_qhw_records()


def test_qhw_target_keeps_named_nodes_loci_timing_and_calibration():
	device, coupling, calibration = records()
	target = qhw_to_qiskit_target(device, coupling, calibration)
	assert target.num_qubits == 2
	assert target["cz"][(0, 1)].duration == 4e-8
	assert target["cz"][(0, 1)].error == pytest.approx(0.01)
	assert (1, 0) not in target["cz"]
	assert target.qubit_properties[0].t1 == 5e-05
	assert coupling["coupling"]["nodes"] == ("QB1", "QB7")
	assert coupling["raw"] == {"provider": "unchanged"}


def test_qhw_target_expands_symmetric_locus_for_undirected_coupling():
	device, coupling, calibration = records()
	coupling = dict(coupling)
	coupling["coupling"] = dict(coupling["coupling"], directed=False)
	target = qhw_to_qiskit_target(device, coupling, calibration)
	assert (0, 1) in target["cz"]
	assert (1, 0) in target["cz"]


def test_qhw_target_keeps_optional_calibration_absent():
	device, coupling, _calibration = records()
	target = qhw_to_qiskit_target(device, coupling)
	assert target["cz"][(0, 1)].error is None
	assert target["cz"][(0, 1)].duration == 4e-8


def test_qhw_target_rejects_missing_required_loci():
	device, coupling, calibration = records()
	coupling = dict(coupling)
	coupling["operations"] = ({"name": "cz", "arity": 2},)
	with pytest.raises(ValueError, match="no supported loci"):
		qhw_to_qiskit_target(device, coupling, calibration)


def test_qhw_target_rejects_unqualified_qiskit(monkeypatch):
	device, coupling, calibration = records()
	monkeypatch.setattr(metadata, "version", lambda name: "99.0")
	with pytest.raises(RuntimeError, match="unsupported Qiskit version"):
		qhw_to_qiskit_target(device, coupling, calibration)
	assert metadata.version("qiskit") == "99.0"
	assert SUPPORTED_QISKIT_VERSIONS == frozenset({"2.2.3"})
