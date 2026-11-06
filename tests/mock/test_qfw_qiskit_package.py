# qfw_qiskit loads each of its names on first use.

import pytest

import qfw_qiskit


@pytest.mark.parametrize("name", qfw_qiskit.__all__)
def test_every_exported_name_resolves(name):
	assert getattr(qfw_qiskit, name).__name__ == name


def test_an_unknown_name_is_an_attribute_error():
	with pytest.raises(AttributeError, match="QFwSimulator"):
		qfw_qiskit.QFwSimulator
