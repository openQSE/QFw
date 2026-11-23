"""
Mock tests for svc_qblox_qpm.

Pure-Python half: parsing, counts and identity resolution, none of which
need the Qblox stack, so these run in ci-mock alongside every other service.

The schedule tests need the real qblox-scheduler and Qiskit and live in
tests/qblox/, which ci-qblox runs. They cannot live here: this suite's
conftest installs Qiskit and NumPy stubs that would shadow the real
packages.
"""

import os
import time

import pytest

from svc_qblox_qpm import util_qblox


# --------------------------------------------------------------------------
# parse_env_file
# --------------------------------------------------------------------------

def test_parse_env_file_reads_quotes_comments_and_blanks(tmp_path):
	path = tmp_path / ".env"
	path.write_text(
		"# a comment\n"
		"\n"
		'cluster_ip = "192.0.2.10"\n'
		"cluster_name = cluster0   # inline comment\n"
		"export QBLOX_DEVICE_CONFIG='/tmp/dev.json'  \n"
		"empty =\n")

	parsed = util_qblox.parse_env_file(str(path))

	# Keys upper-cased, short spellings aliased onto the QBLOX_* names.
	assert parsed["QBLOX_CLUSTER_IP"] == "192.0.2.10"
	# An inline comment is not part of the value; without this an address
	# would be dialled with the comment glued on.
	assert parsed["QBLOX_CLUSTER_NAME"] == "cluster0"
	# "export " dropped, quotes stripped, trailing spaces gone.
	assert parsed["QBLOX_DEVICE_CONFIG"] == "/tmp/dev.json"
	assert parsed["EMPTY"] == ""
	assert "cluster_ip" not in parsed
	# A quoted value keeps a '#' verbatim.
	assert util_qblox.parse_env_value('"a#b"  # trailing') == "a#b"


def test_load_env_file_does_not_override_the_real_environment(
		tmp_path, monkeypatch):
	path = tmp_path / ".env"
	path.write_text('QBLOX_CLUSTER_NAME = "from_file"\n')
	monkeypatch.setenv("QBLOX_CLUSTER_NAME", "from_operator")

	util_qblox.load_env_file(str(path))

	# The site manifest and anything exported before qfw-setup outrank a
	# file shipped in the service package.
	assert os.environ["QBLOX_CLUSTER_NAME"] == "from_operator"


def test_load_env_file_treats_none_as_unset(tmp_path, monkeypatch):
	path = tmp_path / ".env"
	path.write_text("QBLOX_CLUSTER_IP = None\n")
	monkeypatch.delenv("QBLOX_CLUSTER_IP", raising=False)

	util_qblox.load_env_file(str(path))

	# "None" selects the dummy cluster; it must not become the literal
	# string "None" and be dialled as an address.
	assert "QBLOX_CLUSTER_IP" not in os.environ


# --------------------------------------------------------------------------
# build_counts: Qiskit bit order
# --------------------------------------------------------------------------

def test_build_counts_puts_classical_bit_zero_on_the_right():
	# One shot. Channel 0 -> clbit 0 reads |1>, the other two read |0>.
	channel_values = {0: [1.0], 1: [-1.0], 2: [-1.0]}
	acq_channel_to_clbit = {0: 0, 1: 1, 2: 2}

	counts = util_qblox.build_counts(
		channel_values, shots=1,
		acq_channel_to_clbit=acq_channel_to_clbit, num_clbits=3)

	# Qiskit writes clbit 0 last, so this is "001", not "100".
	assert counts == {"001": 1}


def test_build_counts_follows_the_clbit_mapping_not_the_channel_order():
	# Routing can send acq_channel 0 to clbit 2 and vice versa.
	channel_values = {0: [1.0], 1: [-1.0], 2: [-1.0]}
	acq_channel_to_clbit = {0: 2, 1: 1, 2: 0}

	counts = util_qblox.build_counts(
		channel_values, shots=1,
		acq_channel_to_clbit=acq_channel_to_clbit, num_clbits=3)

	assert counts == {"100": 1}


def test_build_counts_leaves_unmeasured_clbits_zero():
	# A 3-bit register with only clbit 1 measured.
	channel_values = {0: [1.0]}
	acq_channel_to_clbit = {0: 1}

	counts = util_qblox.build_counts(
		channel_values, shots=1,
		acq_channel_to_clbit=acq_channel_to_clbit, num_clbits=3)

	assert counts == {"010": 1}


def test_build_counts_thresholds_both_encodings():
	# The scheduler dataset returns -1/+1; raw QRM bins return 0/1.
	for low, high in ((-1.0, 1.0), (0.0, 1.0)):
		counts = util_qblox.build_counts(
			{0: [high, low]}, shots=2,
			acq_channel_to_clbit={0: 0}, num_clbits=1)
		assert counts == {"1": 1, "0": 1}


def test_build_counts_accumulates_over_shots():
	channel_values = {0: [1.0, 1.0, -1.0], 1: [1.0, 1.0, -1.0]}
	acq_channel_to_clbit = {0: 0, 1: 1}

	counts = util_qblox.build_counts(
		channel_values, shots=3,
		acq_channel_to_clbit=acq_channel_to_clbit, num_clbits=2)

	assert counts == {"11": 2, "00": 1}
	assert sum(counts.values()) == 3


# --------------------------------------------------------------------------
# device_coupling_map
# --------------------------------------------------------------------------

def test_device_coupling_map_is_indexed_and_symmetric():
	device_cfg = {
		"elements": {"q0": {}, "q1": {}, "q2": {}},
		"edges": {"q0_q1": {}, "q0_q2": {}},
	}

	coupling = util_qblox.device_coupling_map(device_cfg)

	assert sorted(coupling) == [[0, 1], [0, 2], [1, 0], [2, 0]]


def test_device_coupling_map_skips_edges_naming_unknown_elements():
	device_cfg = {
		"elements": {"q0": {}, "q1": {}},
		"edges": {"q0_q1": {}, "q0_q9": {}, "nonsense": {}},
	}

	assert sorted(util_qblox.device_coupling_map(device_cfg)) == [
		[0, 1], [1, 0]]


# --------------------------------------------------------------------------
# resolve_service_identity: what each config source advertises
# --------------------------------------------------------------------------

def _clear_qblox_env(monkeypatch):
	for name in (
			"QBLOX_CLUSTER_IP", "QBLOX_CLUSTER_NAME", "QBLOX_ENV_FILE",
			"QFW_QPU_DEVICE_ID", "QFW_DEVICE_ACCESS_CFG",
			"QFW_QBLOX_TARGET_ID"):
		monkeypatch.delenv(name, raising=False)


def test_identity_is_dummy_when_nothing_configures_an_address(monkeypatch):
	_clear_qblox_env(monkeypatch)

	identity = util_qblox.resolve_service_identity()

	assert identity["is_dummy"] is True
	assert identity["cluster_ip"] is None
	assert identity["source"] == "none"


def test_identity_is_hardware_from_the_environment(monkeypatch):
	_clear_qblox_env(monkeypatch)
	monkeypatch.setenv("QBLOX_CLUSTER_IP", "192.0.2.10")

	identity = util_qblox.resolve_service_identity()

	assert identity["is_dummy"] is False
	assert identity["cluster_ip"] == "192.0.2.10"
	assert identity["source"] == "environment"


def test_identity_is_hardware_from_device_access(monkeypatch, tmp_path):
	"""
	The reported bug: a site cluster service is given its address only
	through device-access.yaml, by way of device-id, and never sets
	QBLOX_CLUSTER_IP. It must still come up as hardware.
	"""
	pytest.importorskip("yaml")
	_clear_qblox_env(monkeypatch)

	access = tmp_path / "device-access.yaml"
	access.write_text(
		"qpus:\n"
		"  qblox-cluster:\n"
		"    provider: qblox\n"
		"    provider-device-id: cluster0\n"
		"    url: 192.0.2.10\n"
		"    credential-provider: qblox-no-secret\n"
		"credential-providers:\n"
		"  qblox-no-secret:\n"
		"    type: no-secret\n")
	monkeypatch.setenv("QFW_DEVICE_ACCESS_CFG", str(access))
	monkeypatch.setenv("QFW_QPU_DEVICE_ID", "qblox-cluster")

	identity = util_qblox.resolve_service_identity()

	assert identity["is_dummy"] is False
	assert identity["cluster_ip"] == "192.0.2.10"
	assert identity["source"] == "device-access"


def test_identity_never_raises_on_an_unreadable_config(monkeypatch):
	_clear_qblox_env(monkeypatch)
	monkeypatch.setenv("QFW_DEVICE_ACCESS_CFG", "/nonexistent/device.yaml")
	monkeypatch.setenv("QFW_QPU_DEVICE_ID", "qblox-cluster")

	identity = util_qblox.resolve_service_identity()

	# A service that cannot resolve still has to start and report something.
	assert identity["is_dummy"] is True


# --------------------------------------------------------------------------
# QFW_QBLOX_JOB_TIMEOUT
# --------------------------------------------------------------------------

class _HangingAgent:
	"""An agent whose run() outlives the timeout."""

	def __init__(self, seconds):
		self.seconds = seconds
		self.stopped = False
		self.instrument_coordinator = self

	def run(self, _schedule):
		time.sleep(self.seconds)
		return "finished"

	def stop(self, allow_failure=False):
		self.stopped = True


def _client_with_timeout(monkeypatch, timeout):
	monkeypatch.setenv("QFW_QBLOX_JOB_TIMEOUT", str(timeout))
	return util_qblox.QbloxServiceClient()


def test_job_timeout_releases_the_caller_without_waiting_for_the_run(
		monkeypatch):
	"""
	The point of the timeout: a hung instrument must not hold the execute
	lock. A ThreadPoolExecutor used as a context manager would join the
	worker on exit and block for the full run, which looks like it works
	until something actually hangs.
	"""
	from defw_exception import DEFwExecutionError

	client = _client_with_timeout(monkeypatch, 0.2)
	agent = _HangingAgent(seconds=5.0)
	client._agent = agent

	started = time.monotonic()
	with pytest.raises(DEFwExecutionError) as excinfo:
		client._run_with_timeout(agent, schedule=None)
	elapsed = time.monotonic() - started

	assert "QFW_QBLOX_JOB_TIMEOUT" in str(excinfo.value)
	# Released at the timeout, not after the 5s run.
	assert elapsed < 2.0, (
		f"caller held for {elapsed:.2f}s on a 0.2s timeout; the executor "
		"is joining the worker")


def test_job_timeout_drops_the_agent_and_stops_the_instrument(monkeypatch):
	from defw_exception import DEFwExecutionError

	client = _client_with_timeout(monkeypatch, 0.2)
	agent = _HangingAgent(seconds=3.0)
	client._agent = agent

	with pytest.raises(DEFwExecutionError):
		client._run_with_timeout(agent, schedule=None)

	# The next circuit must rebuild rather than inherit a half-run agent.
	assert client._agent is None
	assert agent.stopped is True


def test_job_timeout_of_zero_runs_without_a_worker(monkeypatch):
	client = _client_with_timeout(monkeypatch, 0)
	agent = _HangingAgent(seconds=0.0)

	assert client._run_with_timeout(agent, schedule=None) == "finished"


def test_a_run_inside_the_timeout_returns_its_result(monkeypatch):
	client = _client_with_timeout(monkeypatch, 10)
	agent = _HangingAgent(seconds=0.0)

	assert client._run_with_timeout(agent, schedule=None) == "finished"
	# Nothing was abandoned.
	assert agent.stopped is False
