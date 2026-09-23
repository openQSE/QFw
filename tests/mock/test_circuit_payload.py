# Guards the circuit envelope (util.circuit_payload) and the QPMs that read
# it. A client sends info["circuit"] = {"format": ..., "data": ...} in a
# format the QPM declares under "circuit_formats", or the older info["qasm"].
#
# The CI mock job has no qiskit, so nothing here imports it. QPY loading is
# stubbed, or runs against a stand-in qiskit.qpy module. The real round trips
# are in tests/qiskit/test_circuit_payload_qpy.py.

import base64
import importlib
import sys
import types

import pytest

from defw_exception import DEFwExecutionError
from util import circuit_payload


def _qpy_info(data=b"QISKIT-qpy-bytes"):
	return {"circuit": {
		"format": "qpy",
		"data": base64.b64encode(data).decode("ascii"),
	}}


def _fake_qiskit(monkeypatch, version=13, circuits=("loaded",), error=None):
	# A stand-in for qiskit.qpy, so the loader and the declaration behave the
	# same with or without a real qiskit installed.
	qpy = types.ModuleType("qiskit.qpy")
	qpy.QPY_VERSION = version

	def load(stream):
		if error is not None:
			raise error
		stream.read()
		return list(circuits)

	qpy.load = load
	qiskit = types.ModuleType("qiskit")
	qiskit.qpy = qpy
	monkeypatch.setitem(sys.modules, "qiskit", qiskit)
	monkeypatch.setitem(sys.modules, "qiskit.qpy", qpy)


class _Circuit:
	def __init__(self, info, cid=7):
		self.info = info
		self._cid = cid
		self.states = []

	def get_cid(self):
		return self._cid

	def set_launching(self):
		self.states.append("launching")

	def set_running(self):
		self.states.append("running")


class _Stop(Exception):
	pass


def _capture_transcoder(monkeypatch, module):
	# Stand in for build_iqm_circuit, record what the QPM hands it, and stop
	# run_circuit there, before anything talks to a device.
	seen = []

	def build(source, dynamic, mapping, **kwargs):
		seen.append(source)
		raise _Stop()

	monkeypatch.setattr(module, "build_iqm_circuit", build)
	return seen


# --- the envelope -----------------------------------------------------------

def test_legacy_qasm_is_read_as_openqasm2():
	assert circuit_payload.circuit_payload({"qasm": "OPENQASM 2.0;"}) == (
		"openqasm2", "OPENQASM 2.0;")


def test_the_declared_circuit_wins_over_legacy_qasm():
	info = {
		"qasm": "OPENQASM 2.0; // old",
		"circuit": {"format": "openqasm2", "data": "OPENQASM 2.0; // new"},
	}
	assert circuit_payload.circuit_payload(info) == (
		"openqasm2", "OPENQASM 2.0; // new")


def test_qpy_data_arrives_as_the_original_bytes():
	assert circuit_payload.circuit_payload(_qpy_info(b"\x00QPY\xff")) == (
		"qpy", b"\x00QPY\xff")


@pytest.mark.parametrize("info,message", [
	({}, "carries no circuit"),
	({"circuit": "OPENQASM 2.0;"}, "must be a mapping"),
	({"circuit": {"format": "qir", "data": "x"}}, "unknown circuit format"),
	({"circuit": {"format": "qpy"}}, "carries no qpy data"),
	({"circuit": {"format": "qpy", "data": b"raw"}}, "carries no qpy data"),
	({"circuit": {"format": "qpy", "data": "not base64!"}}, "not valid base64"),
])
def test_malformed_circuits_are_rejected_clearly(info, message):
	with pytest.raises(DEFwExecutionError, match=message):
		circuit_payload.circuit_payload(info)


def test_an_openqasm2_only_qpm_reads_both_forms():
	assert circuit_payload.openqasm2_text({"qasm": "A"}) == "A"
	assert circuit_payload.openqasm2_text(
		{"circuit": {"format": "openqasm2", "data": "B"}}) == "B"


def test_an_openqasm2_only_qpm_rejects_qpy_by_name():
	with pytest.raises(
			DEFwExecutionError,
			match="OpenQASM 2 only and received a qpy circuit"):
		circuit_payload.openqasm2_text(_qpy_info())


def test_the_scheduler_bytes_are_the_submitted_circuit():
	assert circuit_payload.payload_bytes(
		{"qasm": "OPENQASM 2.0;"}) == b"OPENQASM 2.0;"
	assert circuit_payload.payload_bytes(_qpy_info(b"\x01\x02")) == b"\x01\x02"
	assert circuit_payload.payload_bytes({"num_shots": 10}) is None


def test_qiskit_input_keeps_openqasm2_as_text():
	assert circuit_payload.qiskit_input({"qasm": "OPENQASM 2.0;"}) == \
		"OPENQASM 2.0;"


def test_qiskit_input_loads_qpy(monkeypatch):
	loaded = []
	monkeypatch.setattr(
		circuit_payload, "load_qpy",
		lambda data: loaded.append(data) or "circuit-object")

	assert circuit_payload.qiskit_input(_qpy_info(b"\x05")) == "circuit-object"
	assert loaded == [b"\x05"]


# --- loading QPY ------------------------------------------------------------

def test_load_qpy_returns_the_one_circuit(monkeypatch):
	_fake_qiskit(monkeypatch)
	assert circuit_payload.load_qpy(b"x") == "loaded"


def test_load_qpy_rejects_more_than_one_circuit(monkeypatch):
	_fake_qiskit(monkeypatch, circuits=("a", "b"))
	with pytest.raises(DEFwExecutionError, match="exactly one circuit, not 2"):
		circuit_payload.load_qpy(b"x")


def test_unreadable_qpy_names_the_version_this_qpm_reads(monkeypatch):
	# A client that wrote a newer QPY than the QPM's Qiskit reads is told
	# which version it can use instead.
	_fake_qiskit(
		monkeypatch, version=13,
		error=ValueError("QPY version 99 is not supported"))
	with pytest.raises(DEFwExecutionError, match="up to version 13"):
		circuit_payload.load_qpy(b"x")


def test_qpy_without_qiskit_says_so(monkeypatch):
	monkeypatch.setitem(sys.modules, "qiskit", None)
	with pytest.raises(DEFwExecutionError, match="needs qiskit"):
		circuit_payload.load_qpy(b"x")


# --- what a QPM declares ----------------------------------------------------

def test_a_qiskit_qpm_declares_qpy_first_with_its_version(monkeypatch):
	_fake_qiskit(monkeypatch, version=13)
	assert circuit_payload.qiskit_circuit_formats() == {
		"circuit_formats": ["qpy", "openqasm2"],
		"qpy_version": 13,
	}


def test_without_qiskit_only_openqasm2_is_declared(monkeypatch):
	monkeypatch.setitem(sys.modules, "qiskit", None)
	assert circuit_payload.qiskit_circuit_formats() == {
		"circuit_formats": ["openqasm2"]}


def _query_helper(properties):
	from api_qpm_common import QPMCapability, QPMType
	from util.qpm.util_qpm import UTIL_QPM

	class MetadataQPM(UTIL_QPM):
		def controller_telemetry(self):
			return {}

	return MetadataQPM.__new__(MetadataQPM).query_helper(
		QPMType.QPM_TYPE_SIMULATOR,
		QPMCapability.QPM_CAP_STATEVECTOR,
		"QPM", "Quantum Platform Manager",
		properties=properties)


def test_every_qpm_declares_openqasm2_by_default():
	info = _query_helper({"provider": "nwqsim"})
	assert info["properties"]["circuit_formats"] == ["openqasm2"]


def test_a_qpm_can_declare_its_own_formats():
	info = _query_helper({
		"provider": "iqm", "circuit_formats": ["qpy", "openqasm2"]})
	assert info["properties"]["circuit_formats"] == ["qpy", "openqasm2"]


@pytest.mark.parametrize("package", ["svc_iqm_qpm", "svc_lib_qpm"])
def test_the_iqm_qpms_declare_qpy(monkeypatch, package):
	module = importlib.import_module(f"{package}.svc_qpm")
	monkeypatch.delenv("QFW_QPU_DEVICE_ID", raising=False)
	_fake_qiskit(monkeypatch, version=13)
	qpm = module.QPM.__new__(module.QPM)
	qpm.controller_telemetry = lambda: {}

	properties = qpm.query()["properties"]

	assert properties["circuit_formats"] == ["qpy", "openqasm2"]
	assert properties["qpy_version"] == 13


def test_service_configuration_overrides_the_iqm_declaration(monkeypatch):
	import svc_lib_qpm
	from svc_lib_qpm.svc_qpm import QPM
	monkeypatch.delenv("QFW_QPU_DEVICE_ID", raising=False)
	_fake_qiskit(monkeypatch, version=13)
	configured = dict(svc_lib_qpm.svc_info)
	configured["properties"] = {
		**configured.get("properties", {}),
		"circuit_formats": ["openqasm2"],
	}
	monkeypatch.setattr(svc_lib_qpm, "svc_info", configured)
	qpm = QPM.__new__(QPM)
	qpm.controller_telemetry = lambda: {}

	assert qpm.query()["properties"]["circuit_formats"] == ["openqasm2"]


# --- the QPMs that read circuits --------------------------------------------

def test_the_qrmi_driver_transcodes_the_declared_circuit(monkeypatch):
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	seen = _capture_transcoder(monkeypatch, iqm_transcode)
	monkeypatch.setattr(
		circuit_payload, "load_qpy", lambda data: ("loaded", data))
	driver = QrmiDriver({"provider": "iqm"})
	driver._resource = lambda: object()
	driver._target = lambda credential=None: {
		"dynamic_quantum_architecture": {"qubits": ["QB1"]}}

	for info in (_qpy_info(b"\x07"), {"qasm": "OPENQASM 2.0;"}):
		with pytest.raises(_Stop):
			driver.run_circuit(_Circuit(info))

	assert seen == [("loaded", b"\x07"), "OPENQASM 2.0;"]


def test_the_qdmi_driver_transcodes_the_declared_circuit(monkeypatch):
	import util.iqm_transcode as iqm_transcode
	from svc_lib_qpm.drivers import qdmi_driver
	seen = _capture_transcoder(monkeypatch, iqm_transcode)
	monkeypatch.setattr(
		circuit_payload, "load_qpy", lambda data: ("loaded", data))
	monkeypatch.setattr(
		qdmi_driver.fomac_normalize, "extract_topology",
		lambda device: {"qubits": ["QB1"]})
	driver = qdmi_driver.QdmiDriver({"provider": "iqm"})
	driver._device = lambda: object()
	driver._ids = lambda: ("iqm", "device-a")

	for info in (_qpy_info(b"\x08"), {"qasm": "OPENQASM 2.0;"}):
		with pytest.raises(_Stop):
			driver.run_circuit(_Circuit(info))

	assert seen == [("loaded", b"\x08"), "OPENQASM 2.0;"]


def test_the_native_iqm_qpm_transcodes_the_declared_circuit(monkeypatch):
	from svc_iqm_qpm import util_iqm
	seen = _capture_transcoder(monkeypatch, util_iqm)
	monkeypatch.setattr(
		circuit_payload, "load_qpy", lambda data: ("loaded", data))
	service = util_iqm.IQMServiceClient.__new__(util_iqm.IQMServiceClient)
	service._job_timeout = 1.0
	service.client = lambda credential=None: object()
	service.get_dynamic_architecture = (
		lambda calibration_set_id, credential=None: {"qubits": ["QB1"]})

	for info in (_qpy_info(b"\x09"), {"qasm": "OPENQASM 2.0;"}):
		with pytest.raises(_Stop):
			service.run_circuit(_Circuit(info))

	assert seen == [("loaded", b"\x09"), "OPENQASM 2.0;"]


def test_the_scheduler_task_carries_the_submitted_bytes():
	from util.qpm.controller import QPMTargetController
	controller = types.SimpleNamespace(
		canonicalize_external_id=lambda kind, value: 0)
	runtime = types.SimpleNamespace(
		qtask_id=1, canonical_ids={}, reservation_id="reservation-1")

	def payload(info):
		return QPMTargetController._scheduler_task_desc(
			controller, types.SimpleNamespace(info=info),
			runtime)["payload"]

	assert payload(_qpy_info(b"\x01\x02")) == b"\x01\x02"
	assert payload({"qasm": "OPENQASM 2.0;"}) == b"OPENQASM 2.0;"
	assert payload({}) is None


def _simulator_runner(monkeypatch, tmp_path, launched):
	import util.qpm.util_qrc as util_qrc

	class _Runner(util_qrc.UTIL_QRC):
		def __del__(self):
			# Built without __init__, so there is no worker pool to stop.
			pass

	monkeypatch.setattr(
		util_qrc.cdefw_global, "get_defw_tmp_dir", lambda: str(tmp_path),
		raising=False)
	runner = _Runner.__new__(_Runner)
	runner.launcher = types.SimpleNamespace(
		launch=lambda cmd: launched.append(cmd) or 4242)
	runner.form_cmd = lambda circ, qasm_file: ["simulator", qasm_file]
	return runner


def test_a_simulator_runs_an_openqasm2_envelope(monkeypatch, tmp_path):
	launched = []
	runner = _simulator_runner(monkeypatch, tmp_path, launched)
	circuit = _Circuit(
		{"circuit": {"format": "openqasm2", "data": "OPENQASM 2.0;\n"}})

	task = runner.run_circuit_async(circuit)

	with open(task["qasm_file"], encoding="utf-8") as stream:
		assert stream.read() == "OPENQASM 2.0;\n"
	assert launched == [["simulator", task["qasm_file"]]]


def test_a_simulator_rejects_qpy_before_launching_anything(
		monkeypatch, tmp_path):
	launched = []
	runner = _simulator_runner(monkeypatch, tmp_path, launched)
	circuit = _Circuit(_qpy_info())

	with pytest.raises(DEFwExecutionError, match="OpenQASM 2 only"):
		runner.run_circuit_async(circuit)

	assert launched == []
	assert circuit.states == []
	assert list(tmp_path.iterdir()) == []
