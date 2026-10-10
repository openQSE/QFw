# Guards the QDMI driver's Braket profile (drivers/qdmi_profiles.py) and the
# pure parts of util/braket_transcode.py.
#
# MQSC's Amazon Braket QDMI device library maps a QDMI session to one device
# ARN and a QDMI job to one Braket quantum task. Its session parameters are
# aliases of QDMI's: the ARN rides in BASEURL, the Region in CUSTOM2, a
# reservation ARN in CUSTOM3; a job's S3 results URI is job CUSTOM1. It
# authenticates through the AWS SDK's default credential chain, so the
# profile never asks QFw for a token, and it keys counts by measured qubit,
# so the profile maps them back onto the circuit's classical bits.
#
# The device library, mqt.core and the Qiskit transcode are stood in for, so
# no qiskit, amazon-braket-qdmi or mqt-core install is needed. The real
# OpenQASM 3 output is covered by tests/qiskit/test_braket_qasm3.py.

import pathlib
import sys
import types

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers import qdmi_driver  # noqa: E402
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver  # noqa: E402
from svc_lib_qpm.drivers.qdmi_profiles import (  # noqa: E402
	BraketQdmiProfile, profile_for)
from util import braket_transcode  # noqa: E402


SV1_ARN = "arn:aws:braket:::device/quantum-simulator/amazon/sv1"
CEPHEUS_ARN = "arn:aws:braket:us-west-1::device/qpu/rigetti/Cepheus-1-108Q"


def _descriptor(**overrides):
	descriptor = {
		"provider": "aws",
		"id": "aws-sv1",
		"provider_device_id": SV1_ARN,
	}
	descriptor.update(overrides)
	return descriptor


def _module(name, **attrs):
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	return module


# --- choosing and opening ------------------------------------------------------

def test_aws_selects_the_braket_profile():
	assert isinstance(profile_for({"provider": "aws"}), BraketQdmiProfile)
	assert isinstance(profile_for({"provider": "AWS"}), BraketQdmiProfile)


def test_access_comes_from_the_descriptor_alone(monkeypatch):
	# Even with the IQM env vars set, nothing but the descriptor is read, and
	# the credential DB is never consulted: the AWS identity is the process's.
	monkeypatch.setenv("QFW_QC_URL", "https://qc.example.org/")
	monkeypatch.setenv("QFW_API_KEY", "secret")
	import util.device_access as device_access
	monkeypatch.setattr(
		device_access, "resolve_device_access",
		lambda **kwargs: pytest.fail("device access was resolved"))

	access = profile_for(_descriptor()).access()

	assert access == {
		"base_url": SV1_ARN,
		"qdmi_device_id": "amazon.braket.default",
		"region": None,
		"reservation_arn": None,
	}


def test_access_needs_a_device_arn():
	with pytest.raises(DEFwExecutionError, match="device ARN"):
		profile_for(_descriptor(provider_device_id="sv1")).access()
	with pytest.raises(DEFwExecutionError, match="device ARN"):
		profile_for(_descriptor(provider_device_id=None)).access()


def test_the_session_parameters_follow_the_library_aliases():
	profile = profile_for(_descriptor(
		qdmi_device_id="amazon.braket.sv1", aws_region="us-east-1",
		reservation_arn="arn:aws:braket:us-east-1:123:reservation/r1"))

	access = profile.access()

	assert access["qdmi_device_id"] == "amazon.braket.sv1"
	# DEVICEARN is BASEURL, REGION is CUSTOM2, RESERVATION_ARN is CUSTOM3.
	assert profile.open_kwargs(access) == {
		"base_url": SV1_ARN,
		"custom2": "us-east-1",
		"custom3": "arn:aws:braket:us-east-1:123:reservation/r1",
	}
	# Without a Region or a reservation, neither slot is set, so the library
	# takes the Region from the ARN.
	assert profile_for(_descriptor()).open_kwargs(
		profile_for(_descriptor()).access()) == {"base_url": SV1_ARN}


def test_the_device_opens_through_the_braket_library(monkeypatch):
	opened, registered = [], []

	class Definition:
		def __init__(self, device_id, library_path, prefix):
			self.device_id = device_id
			self.library_path = library_path
			self.prefix = prefix

	def open_device(device_id, **kwargs):
		opened.append((device_id, kwargs))
		return "braket-device"

	monkeypatch.setitem(sys.modules, "amazon", _module("amazon"))
	monkeypatch.setitem(sys.modules, "amazon.braket", _module("amazon.braket"))
	monkeypatch.setitem(sys.modules, "amazon.braket.qdmi", _module(
		"amazon.braket.qdmi",
		AMAZON_BRAKET_QDMI_LIBRARY_PATH="/opt/libamazon-braket-qdmi-device.so",
		AMAZON_BRAKET_QDMI_PREFIX="AMAZON_BRAKET"))
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module("mqt.core.qdmi"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi.driver", _module(
		"mqt.core.qdmi.driver",
		DeviceDefinition=Definition,
		register_device_if_absent=lambda d: registered.append(d) or True,
		open_device=open_device))
	driver = QdmiDriver(_descriptor(
		qdmi_device_id="amazon.braket.sv1", aws_region="us-east-1"))

	assert driver._device() == "braket-device"

	assert [(d.device_id, d.library_path, d.prefix) for d in registered] == [
		("amazon.braket.sv1", "/opt/libamazon-braket-qdmi-device.so",
			"AMAZON_BRAKET")]
	assert opened == [("amazon.braket.sv1", {
		"base_url": SV1_ARN, "custom2": "us-east-1"})]
	assert "token" not in opened[0][1]


def test_a_missing_braket_library_names_its_package(monkeypatch):
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module("mqt.core.qdmi"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi.driver", _module(
		"mqt.core.qdmi.driver",
		open_device=lambda *a, **k: None,
		register_device_if_absent=lambda d: True))
	monkeypatch.setitem(sys.modules, "amazon", None)
	monkeypatch.setitem(sys.modules, "amazon.braket.qdmi", None)

	with pytest.raises(DEFwExecutionError, match="Install amazon-braket-qdmi"):
		QdmiDriver(_descriptor())._device()


# --- jobs ---------------------------------------------------------------------

def test_job_parameters_follow_the_library_aliases():
	assert profile_for(_descriptor()).job_kwargs({}) == {}
	profile = profile_for(_descriptor(
		s3_results_uri="s3://results/qfw/sv1",
		reservation_arn="arn:aws:braket:us-east-1:123:reservation/r1"))

	# OUTPUTS3URI is job CUSTOM1, RESERVATION_ARN is job CUSTOM3.
	assert profile.job_kwargs({}) == {
		"custom1": "s3://results/qfw/sv1",
		"custom3": "arn:aws:braket:us-east-1:123:reservation/r1",
	}


def test_a_shot_cap_is_enforced_before_submission():
	profile = profile_for(_descriptor(max_shots="1000"))

	assert profile.check_shots(1000) is None
	with pytest.raises(DEFwExecutionError, match="over the 1000-shot cap"):
		profile.check_shots(1001)
	assert profile_for(_descriptor()).check_shots(10**6) is None


def test_the_wait_comes_from_the_device_timeout():
	assert profile_for(_descriptor()).timeout_seconds({}) == 300.0
	profile = profile_for(_descriptor(job_timeout_seconds="3600"))
	assert profile.timeout_seconds({}) == 3600.0
	# A client timeout still wins.
	assert profile.timeout_seconds({"timeout": 30}) == 30.0


def test_no_calibration_slot_and_a_technology_from_the_arn():
	assert profile_for(_descriptor()).calibration_set_slot is None
	assert profile_for(_descriptor()).technology() == "simulator"
	assert profile_for(_descriptor(
		provider_device_id=CEPHEUS_ARN)).technology() == "superconducting"


def test_encode_hands_the_transcoder_the_device_vocabulary(monkeypatch):
	seen = {}

	def to_braket_qasm3(circuit, accepted):
		seen["circuit"] = circuit
		seen["accepted"] = accepted
		return "OPENQASM 3.0;", {"num_clbits": 1, "map": [(0, 0)]}

	monkeypatch.setattr(braket_transcode, "to_braket_qasm3", to_braket_qasm3)
	monkeypatch.setattr(
		braket_transcode, "accepted_operation_names",
		lambda device: {"rx", "ry", "cz"})
	import util.iqm_transcode as iqm_transcode
	monkeypatch.setattr(
		iqm_transcode, "load_qiskit_circuit", lambda source: ("loaded", source))

	encoded = profile_for(_descriptor()).encode(
		None, "OPENQASM 2.0;", {}, object())

	assert encoded == (
		"OPENQASM 3.0;", "QASM3", {"num_clbits": 1, "map": [(0, 0)]})
	assert seen == {
		"circuit": ("loaded", "OPENQASM 2.0;"),
		"accepted": {"rx", "ry", "cz"},
	}


# --- run_circuit through the Braket profile -----------------------------------

class _Job:
	id = "arn:aws:braket:us-east-1:123:quantum-task/t1"
	queue_position = None

	def __init__(self, counts):
		self.counts = counts

	def check(self):
		return "DONE"

	def get_counts(self):
		return self.counts


def test_run_circuit_submits_qasm3_and_remaps_the_counts(monkeypatch):
	monkeypatch.setattr(
		braket_transcode, "accepted_operation_names", lambda device: {"x"})
	# The asymmetric smoke circuit: X on qubit 0, measured into bit 2; the
	# library keys the three measured qubits highest first, so "001".
	monkeypatch.setattr(
		braket_transcode, "to_braket_qasm3",
		lambda circuit, accepted: (
			"OPENQASM 3.0;\nx q[0];",
			{"num_clbits": 3, "map": [(2, 0), (0, 2), (1, 1)]}))
	import util.iqm_transcode as iqm_transcode
	monkeypatch.setattr(iqm_transcode, "load_qiskit_circuit", lambda s: s)
	records = []

	def to_result_record(counts, shots, provider, device_id, **kwargs):
		records.append((counts, shots, provider, device_id, kwargs))
		return {"schema": "qhw-result-v1"}

	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "to_result_record", to_result_record)
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module(
		"mqt.core.qdmi",
		ProgramFormat=types.SimpleNamespace(QASM3="fmt-qasm3")))
	submitted = []

	def submit_job(program, fmt, shots, **kwargs):
		submitted.append((program, fmt, shots, kwargs))
		return _Job({"001": 10})

	driver = QdmiDriver(_descriptor(
		s3_results_uri="s3://results/qfw/sv1", max_shots="1000"))
	driver._device = lambda credential=None: types.SimpleNamespace(submit_job=submit_job)
	circuit = types.SimpleNamespace(
		info={"qasm": "OPENQASM 2.0;", "num_shots": 10, "poll_interval": 0.0},
		get_cid=lambda: "cid-3")

	record = driver.run_circuit(circuit)

	assert record == {"schema": "qhw-result-v1"}
	assert submitted == [(
		"OPENQASM 3.0;\nx q[0];", "fmt-qasm3", 10,
		{"custom1": "s3://results/qfw/sv1"})]
	assert records == [({"100": 10}, 10, "aws", "aws-sv1", {
		"job_id": "arn:aws:braket:us-east-1:123:quantum-task/t1",
		"status": "completed", "queue_position": None})]


def test_run_circuit_refuses_shots_over_the_cap_before_submitting(monkeypatch):
	import util.iqm_transcode as iqm_transcode
	monkeypatch.setattr(iqm_transcode, "load_qiskit_circuit", lambda s: s)
	submitted = []
	driver = QdmiDriver(_descriptor(max_shots="100"))
	driver._device = lambda credential=None: types.SimpleNamespace(
		submit_job=lambda *a, **k: submitted.append(a))
	circuit = types.SimpleNamespace(
		info={"qasm": "OPENQASM 2.0;", "num_shots": 101}, get_cid=lambda: "c")

	with pytest.raises(DEFwExecutionError, match="over the 100-shot cap"):
		driver.run_circuit(circuit)

	assert submitted == []


# --- util.braket_transcode: the pure parts ------------------------------------

@pytest.mark.parametrize("arn,technology", [
	(SV1_ARN, "simulator"),
	("arn:aws:braket:::device/quantum-simulator/amazon/dm1", "simulator"),
	(CEPHEUS_ARN, "superconducting"),
	("arn:aws:braket:eu-north-1::device/qpu/iqm/Garnet", "superconducting"),
	("arn:aws:braket:us-east-1::device/qpu/ionq/Forte-1", "trapped-ion"),
	("arn:aws:braket:eu-north-1::device/qpu/aqt/Ibex-Q1", "trapped-ion"),
	("arn:aws:braket:us-east-1::device/qpu/quera/Aquila", "neutral-atom"),
	("arn:aws:braket:us-east-1::device/qpu/newvendor/X", None),
	("not-an-arn", None),
	(None, None),
])
def test_technology_for_arn(arn, technology):
	assert braket_transcode.technology_for_arn(arn) == technology


class _Operation:
	def __init__(self, name):
		self._name = name

	def name(self):
		return self._name


class _Device:
	def __init__(self, native, supported=None):
		self.native = [_Operation(n) for n in native]
		self.supported = (
			None if supported is None else [_Operation(n) for n in supported])
		self.queried = []

	def operations(self):
		return self.native

	def query_custom_operations(self, custom_property):
		self.queried.append(custom_property)
		return self.supported


def test_accepted_names_join_native_and_supported_operations(monkeypatch):
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module(
		"mqt.core.qdmi",
		CustomProperty=types.SimpleNamespace(CUSTOM1="slot-1")))
	device = _Device(["RX", "ry", "cz"], ["h", "cnot", "rx"])

	names = braket_transcode.accepted_operation_names(device)

	assert names == {"rx", "ry", "cz", "h", "cnot"}
	assert device.queried == ["slot-1"]


def test_accepted_names_survive_a_device_without_custom_operations():
	device = types.SimpleNamespace(operations=lambda: [_Operation("gpi")])

	assert braket_transcode.accepted_operation_names(device) == {"gpi"}


def test_a_failed_operations_query_is_reported_as_itself():
	# Without credentials the library's first AWS call fails here. That is
	# the error to show, not a later complaint about the circuit's gates.
	def operations():
		raise RuntimeError("Querying OPERATIONS: Permission denied.")

	device = types.SimpleNamespace(operations=operations)

	with pytest.raises(DEFwExecutionError, match="Permission denied"):
		braket_transcode.accepted_operation_names(device)


def test_a_device_with_no_operations_is_reported():
	device = types.SimpleNamespace(operations=lambda: [])

	with pytest.raises(DEFwExecutionError, match="reported no operations"):
		braket_transcode.accepted_operation_names(device)


def test_remap_counts_is_identity_when_bits_follow_qubits():
	measurement = {"num_clbits": 2, "map": [(0, 0), (1, 1)]}

	assert braket_transcode.remap_counts(
		{"00": 5, "11": 5}, measurement) == {"00": 5, "11": 5}


def test_remap_counts_follows_the_measurement_map():
	# X on qubit 0 measured into bit 2 (the asymmetric smoke circuit). The
	# library's key is highest measured qubit first: q2 q1 q0 = "001".
	measurement = {"num_clbits": 3, "map": [(2, 0), (0, 2), (1, 1)]}

	assert braket_transcode.remap_counts(
		{"001": 9, "011": 1}, measurement) == {"100": 9, "110": 1}


def test_remap_counts_fills_unmeasured_bits_with_zero():
	# Only qubit 1 is measured, into bit 0 of a 3-bit register.
	measurement = {"num_clbits": 3, "map": [(1, 0)]}

	assert braket_transcode.remap_counts(
		{"1": 7, "0": 3}, measurement) == {"001": 7, "000": 3}


def test_remap_counts_merges_keys_that_land_on_one_bit_pattern():
	# Two qubits measured into the same bit: the later measurement lands,
	# as in Qiskit, so the earlier qubit's value is dropped.
	measurement = {"num_clbits": 1, "map": [(0, 0), (1, 0)]}

	assert braket_transcode.remap_counts(
		{"10": 4, "11": 6}, measurement) == {"1": 10}


def test_remap_counts_rejects_a_key_of_the_wrong_width():
	measurement = {"num_clbits": 2, "map": [(0, 0), (1, 1)]}

	with pytest.raises(DEFwExecutionError, match="does not cover the 2"):
		braket_transcode.remap_counts({"1": 10}, measurement)


@pytest.mark.parametrize("measurement", [None, {}, {"num_clbits": 0}])
def test_remap_counts_passes_through_without_a_map(measurement):
	counts = {"01": 10}

	assert braket_transcode.remap_counts(counts, measurement) is counts
