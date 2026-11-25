"""
extract_channel_values against real NumPy.

Not in tests/mock: that suite's conftest installs a NumPy stub with no
asarray, which is what this function calls.
"""

import pytest

from svc_qblox_qpm import util_qblox


class _FakeDataset:
	"""Stands in for the xarray dataset HardwareAgent.run() returns."""

	def __init__(self, data_vars):
		self.data_vars = data_vars

	def __getitem__(self, key):
		return _FakeVar(self.data_vars[key])


class _FakeVar:
	def __init__(self, values):
		self.values = values


def test_extract_channel_values_reads_only_the_channels_acquired_on():
	# Three channels present, but the circuit only measured two of them.
	dataset = _FakeDataset({0: [1.0, -1.0], 1: [-1.0, -1.0], 2: [9.0, 9.0]})

	got = util_qblox.extract_channel_values(dataset, [0, 1])

	assert sorted(got) == [0, 1]
	assert 2 not in got


def test_extract_channel_values_accepts_string_keys():
	# The dataset names its variables with str keys in some versions.
	dataset = _FakeDataset({"0": [1.0], "1": [-1.0]})

	got = util_qblox.extract_channel_values(dataset, [0, 1])

	assert sorted(got) == [0, 1]


def test_extract_channel_values_fails_loudly_on_a_missing_channel():
	from defw_exception import DEFwExecutionError

	dataset = _FakeDataset({0: [1.0]})

	# Must not silently read the wrong channel.
	with pytest.raises(DEFwExecutionError, match="acq_channel 1 not found"):
		util_qblox.extract_channel_values(dataset, [0, 1])


def test_extract_channel_values_names_what_the_dataset_actually_has():
	from defw_exception import DEFwExecutionError

	dataset = _FakeDataset({"q0_readout": [1.0]})

	with pytest.raises(DEFwExecutionError) as excinfo:
		util_qblox.extract_channel_values(dataset, [0])

	# The error has to carry the real variable names or it is undebuggable.
	assert "q0_readout" in str(excinfo.value)
