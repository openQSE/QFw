from importlib import metadata

import pytest

from qfw_mqt.qhw_target import (
	SUPPORTED_MQT_CORE_VERSIONS,
	qhw_to_mqt_target,
)
from tests.qhw_target_records import representative_qhw_records


def test_qhw_target_keeps_named_nodes_topology_operations_and_calibration():
	device, coupling, calibration = representative_qhw_records()
	target = qhw_to_mqt_target(device, coupling, calibration)
	operations = {operation.name: operation for operation in target.operations}
	assert target.name == "test"
	assert [site.name for site in target.sites] == ["QB1", "QB7"]
	assert target.couplings == [(0, 1)]
	assert target.sites[0].t1 == 50000000
	assert operations["cz"].site_tuples[0].sites == [0, 1]
	assert operations["cz"].site_tuples[0].duration == 40000
	assert operations["cz"].site_tuples[0].fidelity == 0.99
	assert operations["rz"].parameter_bounds == [(-3.2, 3.2)]
	assert coupling["coupling"]["nodes"] == ("QB1", "QB7")
	assert coupling["raw"] == {"provider": "unchanged"}


def test_qhw_target_accepts_optional_calibration_absence():
	device, coupling, _calibration = representative_qhw_records()
	target = qhw_to_mqt_target(device, coupling)
	cz = next(operation for operation in target.operations
		if operation.name == "cz")
	assert cz.site_tuples[0].duration == 40000
	assert cz.site_tuples[0].fidelity is None


def test_qhw_target_rejects_missing_explicit_connectivity():
	device, coupling, calibration = representative_qhw_records()
	coupling = dict(coupling)
	coupling["coupling"] = dict(coupling["coupling"], edges=())
	with pytest.raises(ValueError, match="no explicit edges"):
		qhw_to_mqt_target(device, coupling, calibration)


def test_qhw_target_rejects_unqualified_mqt_core(monkeypatch):
	device, coupling, calibration = representative_qhw_records()
	monkeypatch.setattr(metadata, "version", lambda name: "99.0")
	with pytest.raises(RuntimeError, match="unsupported MQT Core version"):
		qhw_to_mqt_target(device, coupling, calibration)
	assert SUPPORTED_MQT_CORE_VERSIONS == frozenset({"4.1.1"})
