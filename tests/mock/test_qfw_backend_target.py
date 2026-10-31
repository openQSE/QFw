# The Qiskit client's device target: QFwBackend.target built from the QPM's
# telemetry binding (get_backend_info and the qhw records behind it), and the
# generic target it keeps, with a logged reason, when the QPM has no device to
# describe or cannot be asked.
import logging

from tests.mock.fakes import FakeEventAPI, FakeQPM, FakeRuntime


class FakeTelemetryAPI:
	# The managed telemetry binding: the five introspection calls, each
	# answering a canned record or raising a configured failure.
	def __init__(self, backend_info=None, device_info=None,
			coupling_graph=None, calibration=None, failures=None):
		self.records = {
			"get_backend_info": backend_info,
			"get_device_info": device_info,
			"get_coupling_graph": coupling_graph,
			"get_calibration_snapshot": calibration,
			"get_dynamic_backend_info": None,
		}
		self.failures = dict(failures or {})
		self.calls = []

	def __getattr__(self, name):
		if name not in self.records:
			raise AttributeError(name)

		def call(**kwargs):
			self.calls.append((name, kwargs))
			if name in self.failures:
				raise self.failures[name]
			return self.records[name]
		return call

	def names(self):
		return [name for name, _kwargs in self.calls]


class FakeManagedBinding:
	# The lifecycle binding behind a managed QPM client: hands out an API by
	# binding name, as api_qpm_common.QPMLifecycleBinding does from the
	# directory's service record.
	def __init__(self, telemetry):
		self.telemetry = telemetry
		self.api_requests = []
		self.listeners = []
		self.closed = False

	def api(self, binding_name, expected_runtime_id=None):
		self.api_requests.append((binding_name, expected_runtime_id))
		if binding_name != "telemetry":
			raise AssertionError(f"unexpected binding {binding_name!r}")
		return self.telemetry

	def add_reconnect_listener(self, listener):
		self.listeners.append(listener)

	def remove_reconnect_listener(self, listener):
		self.listeners.remove(listener)

	def close(self):
		self.closed = True


FAKE_IQM_INFO = {
	"backend": "fake-iqm",
	"target_id": "fake-iqm-20q",
	"metadata_supported": True,
}
FAKE_IQM_DEVICE = {
	"backend": "fake-iqm",
	"target_id": "fake-iqm-20q",
	"num_qubits": 4,
	"max_shots": 10000,
	"metadata_supported": True,
}
FAKE_IQM_COUPLING = {
	"backend": "fake-iqm",
	"target_id": "fake-iqm-20q",
	"calibration_set_id": None,
	"couplings": [[0, 1], [1, 2], [2, 3]],
}
FAKE_IQM_CALIBRATION = {
	"backend": "fake-iqm",
	"target_id": "fake-iqm-20q",
	"calibration_set_id": None,
}

IQM_QUBITS = ["QB1", "QB2", "QB3", "QB4"]
IQM_EDGES = [["QB1", "QB2"], ["QB2", "QB3"], ["QB3", "QB4"]]


def iqm_records():
	# The shim's records for a four-qubit IQM-shaped device, in the shapes
	# qhw_iqm produces from QRMI's target(): the device record embedded in
	# get_backend_info, the coupling record with IQM's operation names and
	# loci (reset_wait also lists couplers and probe lines), and the
	# calibration snapshot keeping IQM's observation sets whole.
	device = {
		"schema": "qhw-device-v1",
		"provider": "iqm",
		"device": {"id": "ornl-4q", "num_qubits": 4},
		"qubits": [
			{"id": qubit, "index": index}
			for index, qubit in enumerate(IQM_QUBITS)],
	}
	info = {
		"backend": "iqm",
		"metadata_supported": True,
		"static_architecture": {
			"qubits": IQM_QUBITS, "connectivity": IQM_EDGES},
		"active_qubits": IQM_QUBITS,
		"calibration_set_id": "cal-1",
		"qhw_device": device,
	}
	single = [[qubit] for qubit in IQM_QUBITS]
	coupling = {
		"schema": "qhw-coupling-v1",
		"provider": "iqm",
		"device": {"id": "ornl-4q"},
		"coupling": {"directed": False, "nodes": IQM_QUBITS, "edges": IQM_EDGES},
		"operations": [
			{"name": "cc_prx", "arity": 1, "supported_loci": single},
			{"name": "cz", "native_name": "crf_crf", "arity": 2,
			 "supported_loci": [["QB2", "QB1"], ["QB2", "QB3"], ["QB4", "QB3"]]},
			{"name": "measure", "arity": 1, "supported_loci": single},
			{"name": "prx", "native_name": "drag_crf_sx", "arity": 1,
			 "supported_loci": single},
			{"name": "reset_wait", "arity": 1,
			 "supported_loci": single + [["TC-1-2"], ["PL-1"]]},
		],
	}

	def observation(dut_field, value, unit="", invalid=False):
		return {"dut_field": dut_field, "value": value, "unit": unit,
			"invalid": invalid}

	calibration = {
		"schema": "qhw-calibration-v1",
		"provider": "iqm",
		"device": {"id": "ornl-4q"},
		"calibration": {"calibration_set_id": "cal-1"},
		"extensions": {"iqm.v1": {
			"quality_metric_set": {"observations": [
				observation(
					"metrics.irb.cz.crf_crf.QB1__QB2.fidelity:par=d2", 0.99),
				observation(
					"metrics.rb.prx.drag_crf_sx.QB1.fidelity:par=d2", 0.999),
				observation("metrics.ssro.measure.constant.QB1.fidelity", 0.98),
				# Not a gate fidelity: the family is not rb/irb/ssro.
				observation("metrics.qndness.measure.constant.QB1.fidelity", 0.5),
				# An invalid observation is not a measurement.
				observation(
					"metrics.rb.prx.drag_crf_sx.QB2.fidelity:par=d2", 0.1,
					invalid=True),
				observation("characterization.model.QB1.t1_time", 50e-6, "s"),
				observation("characterization.model.QB1.t2_time", 30e-6, "s"),
			]},
			"calibration_set": {"observations": [
				# IQM reports some durations with an empty unit: seconds.
				observation("gates.cz.crf_crf.QB2__QB1.duration", 6e-08),
				observation("gates.prx.drag_crf_sx.QB1.duration", 16, "ns"),
				observation("gates.measure.constant.QB1.duration", 5.48e-07, "s"),
			]},
		}},
	}
	return info, device, coupling, calibration


def _backend(monkeypatch, telemetry=None, provider=None, qpm_properties=None,
		**kwargs):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	if telemetry is not None:
		fake_qpm.lifecycle_binding = FakeManagedBinding(telemetry)
		fake_qpm.expected_runtime_id = "qpm-runtime-1"
	if qpm_properties is not None:
		fake_qpm.qpm_properties = qpm_properties
	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
	monkeypatch.setattr(
		qfw_simulator, "BaseEventAPI",
		lambda: FakeEventAPI(class_id="event-api-target"))
	monkeypatch.setattr(
		qfw_simulator, "me", FakeRuntime(endpoint="endpoint-target"))
	monkeypatch.delenv(qfw_simulator.SHIM_LIB_ENV, raising=False)
	return qfw_simulator.QFwBackend(provider=provider, **kwargs)


def _qargs(target, name):
	return set(target.qargs_for_operation_name(name))


def test_backend_binds_the_telemetry_api_next_to_the_execution_api(
		monkeypatch):
	telemetry = FakeTelemetryAPI(backend_info=FAKE_IQM_INFO)
	backend = _backend(monkeypatch, telemetry)

	assert backend.qpm.lifecycle_binding.api_requests == [
		("telemetry", "qpm-runtime-1")]
	assert backend.telemetry is not None
	assert backend.telemetry.get_backend_info() == FAKE_IQM_INFO
	assert backend.telemetry.metadata_supported() is True
	assert telemetry.calls == [
		("get_backend_info", {"lib": None}),
		("get_backend_info", {"lib": None}),
	]
	# Binding the API is not calling it: the backend has not asked for
	# its target.
	assert backend.target_source is None


def test_telemetry_reads_through_the_library_the_shim_describes_with(monkeypatch):
	backend = _backend(monkeypatch)
	backend.options.lib = None
	backend.qpm.qpm_properties = {
		"provider": "shim", "description_library": "qrmi"}

	assert backend._telemetry_context()["lib"] == "qrmi"

	backend.options.lib = "qdmi"
	assert backend._telemetry_context()["lib"] == "qdmi"


def test_telemetry_carries_no_library_when_the_shim_names_none(monkeypatch):
	backend = _backend(monkeypatch)
	backend.options.lib = None
	backend.qpm.qpm_properties = {"provider": "shim"}

	assert backend._telemetry_context()["lib"] is None


def test_telemetry_calls_carry_the_shim_library_and_the_token(monkeypatch):
	telemetry = FakeTelemetryAPI(backend_info=FAKE_IQM_INFO)
	backend = _backend(
		monkeypatch, telemetry, qpm_properties={"provider": "shim"})
	backend.options.lib = "qdmi"
	backend.options.token = "opaque-token"

	backend.telemetry.get_backend_info()
	backend.telemetry.get_coupling_graph(calibration_set_id="cal-2")
	backend.telemetry.get_device_info(lib="qrmi")

	assert telemetry.calls == [
		("get_backend_info", {"lib": "qdmi", "token": "opaque-token"}),
		("get_coupling_graph", {
			"lib": "qdmi", "token": "opaque-token",
			"calibration_set_id": "cal-2"}),
		("get_device_info", {"lib": "qrmi", "token": "opaque-token"}),
	]


def test_reserved_target_passes_reservation_only_to_backend_info(monkeypatch):
	info, device, coupling, calibration = iqm_records()
	telemetry = FakeTelemetryAPI(info, device, coupling, calibration)
	backend = _backend(
		monkeypatch, telemetry, provider="shim",
		qpm_properties={"provider": "shim"})
	backend.options.reservation_id = 123

	target = backend.target

	assert target is not None
	assert telemetry.calls == [
		("get_backend_info", {"lib": None, "reservation_id": 123}),
		("get_device_info", {"lib": None}),
		("get_coupling_graph", {
			"lib": None, "calibration_set_id": None}),
		("get_calibration_snapshot", {
			"lib": None, "calibration_set_id": None}),
	]


def test_target_from_device_metadata_has_the_qubits_gates_and_coupling(
		monkeypatch):
	# The fake IQM QPM describes its device with plain dicts: a qubit count
	# and a couplings list, no gate set. The target takes the count and the
	# edges, and QFw's generic gates on them.
	telemetry = FakeTelemetryAPI(
		FAKE_IQM_INFO, FAKE_IQM_DEVICE, FAKE_IQM_COUPLING,
		FAKE_IQM_CALIBRATION)
	backend = _backend(monkeypatch, telemetry, provider="fake-iqm")

	target = backend.target

	assert telemetry.names() == [
		"get_backend_info", "get_device_info", "get_coupling_graph",
		"get_calibration_snapshot"]
	assert target.num_qubits == 4
	assert target.description == "QFw fake-iqm fake-iqm-20q target"
	assert backend.target_source == "device: QFw fake-iqm fake-iqm-20q target"
	assert {"x", "rz", "cx", "rzz", "measure", "reset"} <= set(
		target.operation_names)
	assert _qargs(target, "x") == {(0,), (1,), (2,), (3,)}
	assert _qargs(target, "cx") == {
		(0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2)}
	assert target.build_coupling_map().get_edges() == [
		(0, 1), (1, 0), (1, 2), (2, 1), (2, 3), (3, 2)]
	# Three-qubit gates have no place on a coupled device; the transpiler
	# decomposes them.
	assert "ccx" not in target.operation_names


def test_target_from_qhw_records_has_native_gates_loci_and_calibration(
		monkeypatch):
	info, device, coupling, calibration = iqm_records()
	telemetry = FakeTelemetryAPI(info, device, coupling, calibration)
	backend = _backend(monkeypatch, telemetry, provider="shim")

	target = backend.target

	assert target.num_qubits == 4
	assert target.description == "QFw iqm ornl-4q target"
	# prx is Qiskit's r, reset_wait its reset; cc_prx is not a circuit
	# instruction and the non-qubit loci of reset_wait are dropped.
	assert set(target.operation_names) == {"r", "cz", "measure", "reset"}
	assert _qargs(target, "r") == {(0,), (1,), (2,), (3,)}
	assert _qargs(target, "reset") == {(0,), (1,), (2,), (3,)}
	# cz is symmetric and the coupling undirected: both orders of each
	# listed locus, and only the listed loci.
	assert _qargs(target, "cz") == {
		(1, 0), (0, 1), (1, 2), (2, 1), (3, 2), (2, 3)}
	# Errors from the quality metrics (1 - fidelity), durations from the
	# calibration set in seconds, by locus, either way round for cz.
	cz = target["cz"][(0, 1)]
	assert abs(cz.error - 0.01) < 1e-12
	assert cz.duration == 6e-08
	assert target["cz"][(1, 0)].error == cz.error
	assert target["cz"][(1, 2)] is None
	prx = target["r"][(0,)]
	assert abs(prx.error - 0.001) < 1e-12
	assert abs(prx.duration - 16e-9) < 1e-18
	# The qndness fidelity and the invalid observation count for nothing.
	measure = target["measure"][(0,)]
	assert abs(measure.error - 0.02) < 1e-12
	assert measure.duration == 5.48e-07
	assert target["r"][(1,)] is None
	assert target["measure"][(1,)] is None


def test_calibration_metrics_read_the_qdmi_extension():
	# The QDMI driver's snapshot carries fidelity and duration per operation
	# locus and coherence per qubit under qdmi.fomac.v1, times in the
	# device's unit.
	from qfw_qiskit.qfw_target import build_device_target, calibration_metrics

	calibration = {
		"schema": "qhw-calibration-v1",
		"extensions": {"qdmi.fomac.v1": {
			"duration_unit": "us",
			"gate_metrics": [
				{"operation": "cz", "locus": ["QB1", "QB2"],
				 "fidelity": 0.995, "duration": 0.08},
				{"operation": "prx", "locus": ["QB1"], "duration": 0.02},
			],
			"qubit_metrics": [{"qubit": "QB1", "t1": 40.0, "t2": 20.0}],
		}},
	}

	gates, qubits = calibration_metrics(calibration)

	assert abs(gates[("cz", ("QB1", "QB2"))]["error"] - 0.005) < 1e-12
	assert abs(gates[("cz", ("QB1", "QB2"))]["duration"] - 8e-8) < 1e-18
	assert "error" not in gates[("prx", ("QB1",))]
	assert abs(gates[("prx", ("QB1",))]["duration"] - 2e-8) < 1e-18
	assert abs(qubits["QB1"]["t1"] - 40e-6) < 1e-15
	assert abs(qubits["QB1"]["t2"] - 20e-6) < 1e-15

	info, device, coupling, _iqm_calibration = iqm_records()
	target = build_device_target(info, device, coupling, calibration)
	assert abs(target["cz"][(0, 1)].error - 0.005) < 1e-12
	assert target["cz"][(1, 2)] is None


def test_unknown_device_operations_stay_in_the_target_as_opaque_gates():
	from qfw_qiskit.qfw_target import build_device_target

	info = {"backend": "aws", "metadata_supported": True}
	device = {"schema": "qhw-device-v1", "provider": "aws",
		  "device": {"id": "Ankaa", "num_qubits": 3},
		  "qubits": [{"id": "0"}, {"id": "1"}, {"id": "2"}]}
	coupling = {
		"schema": "qhw-coupling-v1",
		"coupling": {"directed": False, "nodes": ["0", "1", "2"],
			     "edges": [["0", "1"], ["1", "2"]]},
		"operations": [
			{"name": "cnot", "arity": 2, "supported_loci": [["0", "1"]]},
			{"name": "iswap", "arity": 2, "supported_loci": [["1", "2"]]},
			{"name": "xyz_native", "arity": 1, "parameters": ["theta"],
			 "supported_loci": [["0"], ["1"], ["2"]]},
			{"name": "barrier", "arity": 0, "supported_loci": [[]]},
			{"name": "gpi", "arity": 1, "supported_loci": [[]]},
		],
	}

	target = build_device_target(info, device, coupling, None)

	assert target.num_qubits == 3
	assert set(target.operation_names) == {
		"cx", "iswap", "xyz_native", "gpi"}
	# cnot is Qiskit's cx and directed: the listed order only.
	assert _qargs(target, "cx") == {(0, 1)}
	# iswap is symmetric on an undirected coupling.
	assert _qargs(target, "iswap") == {(1, 2), (2, 1)}
	opaque = target.operation_from_name("xyz_native")
	assert opaque.num_qubits == 1
	assert [parameter.name for parameter in opaque.params] == ["theta"]
	# A global locus means anywhere.
	assert target.qargs_for_operation_name("gpi") is None


def test_ibm_configuration_goes_through_the_runtime_converter(monkeypatch):
	# The shim's IBM record carries the raw BackendConfiguration under
	# static_architecture and the raw BackendProperties under
	# backend_properties. Both reach qiskit_ibm_runtime's converter, which
	# owns the IBM target, and nothing else is fetched.
	import qfw_qiskit.qfw_target as qfw_target

	calls = []

	class FakeModel:
		def __init__(self, data):
			self.data = data

		@classmethod
		def from_dict(cls, data):
			return cls(data)

	class ConvertedTarget:
		description = "converted"
		num_qubits = 2
		operation_names = {"cx", "sx"}

	def convert_to_target(configuration, properties=None):
		calls.append((configuration.data,
			      None if properties is None else properties.data))
		return ConvertedTarget()

	monkeypatch.setattr(
		qfw_target, "_ibm_converter",
		lambda: (convert_to_target, FakeModel, FakeModel))
	configuration = {
		"backend_name": "ibm_fake", "n_qubits": 2, "basis_gates": ["cx", "sx"],
		"coupling_map": [[0, 1]], "gates": []}
	properties = {"backend_name": "ibm_fake", "qubits": [], "gates": []}
	info = {
		"backend": "ibm",
		"metadata_supported": True,
		"static_architecture": configuration,
		"backend_properties": properties,
		"qhw_device": {"qubits": []},
	}
	telemetry = FakeTelemetryAPI(backend_info=info)
	backend = _backend(monkeypatch, telemetry, provider="shim")

	target = backend.target

	assert isinstance(target, ConvertedTarget)
	assert calls == [(configuration, properties)]
	assert telemetry.names() == ["get_backend_info"]
	assert backend.target_source == "device: converted"

	# Without properties the converter gets the configuration alone.
	calls.clear()
	qfw_target.build_device_target(dict(info, backend_properties=None))
	assert calls == [(configuration, None)]


def test_ibm_configuration_without_the_runtime_converter(monkeypatch):
	# A client without qiskit_ibm_runtime still gets the configuration's
	# qubits, basis gates on their loci and the directed coupling map.
	import qfw_qiskit.qfw_target as qfw_target

	monkeypatch.setattr(qfw_target, "_ibm_converter", lambda: None)
	configuration = {
		"backend_name": "ibm_fake",
		"n_qubits": 3,
		"basis_gates": ["cx", "rz", "sx", "x", "ecr_like"],
		"coupling_map": [[0, 1], [1, 0], [1, 2]],
		"gates": [
			{"name": "cx", "parameters": [],
			 "coupling_map": [[0, 1], [1, 0], [1, 2]]},
			{"name": "rz", "parameters": ["theta"],
			 "coupling_map": [[0], [1], [2]]},
			{"name": "ecr_like", "parameters": [], "coupling_map": [[1, 2]]},
		],
	}
	info = {"backend": "ibm", "metadata_supported": True,
		"static_architecture": configuration}

	target = qfw_target.build_device_target(info)

	assert target.description == "QFw ibm ibm_fake target"
	assert target.num_qubits == 3
	assert set(target.operation_names) == {
		"cx", "rz", "sx", "x", "ecr_like", "measure", "reset"}
	assert _qargs(target, "cx") == {(0, 1), (1, 0), (1, 2)}
	assert _qargs(target, "rz") == {(0,), (1,), (2,)}
	assert _qargs(target, "ecr_like") == {(1, 2)}
	assert _qargs(target, "measure") == {(0,), (1,), (2,)}
	# A basis gate the configuration does not describe is offered anywhere.
	assert target.qargs_for_operation_name("sx") is None


def test_target_is_generic_when_the_qpm_reports_no_metadata(
		monkeypatch, caplog):
	telemetry = FakeTelemetryAPI(backend_info={
		"backend": "nwqsim", "metadata_supported": False})
	backend = _backend(monkeypatch, telemetry, provider="nwqsim")

	with caplog.at_level(logging.INFO):
		target = backend.target

	assert target.num_qubits == 400
	assert len(target.operation_names) == 39
	assert telemetry.names() == ["get_backend_info"]
	assert backend.target_source == (
		"generic: QPM nwqsim reports no device metadata")
	assert "reports no device metadata" in caplog.text


def test_target_is_generic_when_the_telemetry_call_fails(monkeypatch, caplog):
	telemetry = FakeTelemetryAPI(
		backend_info=FAKE_IQM_INFO,
		failures={"get_backend_info": RuntimeError("binding unavailable")})
	backend = _backend(monkeypatch, telemetry, provider="fake-iqm",
			   num_qubits=16)

	with caplog.at_level(logging.WARNING):
		target = backend.target

	assert target.num_qubits == 16
	assert backend.target_source == (
		"generic: the QPM's device telemetry failed: binding unavailable")
	assert "binding unavailable" in caplog.text
	assert "generic 16-qubit QFw target" in caplog.text


def test_target_is_generic_without_a_managed_binding(monkeypatch, caplog):
	backend = _backend(monkeypatch)

	assert backend.telemetry is None
	with caplog.at_level(logging.INFO):
		target = backend.target
	assert target.num_qubits == 400
	assert backend.target_source == (
		"generic: the QPM connection has no telemetry binding")
	assert "no telemetry binding" in caplog.text


def test_secondary_records_are_optional(monkeypatch):
	# A QPM that answers get_backend_info but not the coupling graph still
	# describes a device: its qubits, with the generic gates anywhere on
	# them, since the connectivity is unknown.
	telemetry = FakeTelemetryAPI(
		FAKE_IQM_INFO, FAKE_IQM_DEVICE, None, FAKE_IQM_CALIBRATION,
		failures={"get_coupling_graph": RuntimeError("not implemented")})
	backend = _backend(monkeypatch, telemetry, provider="fake-iqm")

	target = backend.target

	assert target.num_qubits == 4
	assert target.build_coupling_map() is None
	assert target.qargs_for_operation_name("cx") is None
	assert "ccx" in target.operation_names
	assert backend.target_source == "device: QFw fake-iqm fake-iqm-20q target"


def test_target_is_built_once_per_backend(monkeypatch):
	telemetry = FakeTelemetryAPI(
		FAKE_IQM_INFO, FAKE_IQM_DEVICE, FAKE_IQM_COUPLING,
		FAKE_IQM_CALIBRATION)
	backend = _backend(monkeypatch, telemetry, provider="fake-iqm")

	first = backend.target
	second = backend.target
	backend.configuration()

	assert first is second
	assert telemetry.names().count("get_backend_info") == 1


def test_an_explicit_target_wins_over_the_device(monkeypatch):
	telemetry = FakeTelemetryAPI(backend_info=FAKE_IQM_INFO)
	explicit = object()
	backend = _backend(monkeypatch, telemetry, target=explicit)

	assert backend.target is explicit
	assert telemetry.calls == []


def test_configuration_reflects_the_device_target(monkeypatch):
	telemetry = FakeTelemetryAPI(
		FAKE_IQM_INFO, FAKE_IQM_DEVICE, FAKE_IQM_COUPLING,
		FAKE_IQM_CALIBRATION)
	backend = _backend(monkeypatch, telemetry, provider="fake-iqm")

	configuration = backend.configuration()

	assert configuration["n_qubits"] == 4
	assert configuration["coupling_map"] == [
		[0, 1], [1, 0], [1, 2], [2, 1], [2, 3], [3, 2]]
	assert "cx" in configuration["basis_gates"]
	assert "ccx" not in configuration["basis_gates"]


def qdmi_records():
	# The shim's records for a three-qubit device read through QDMI: the
	# driver's get_backend_info composite with the fomac device record
	# embedded, the coupling record with the native operations and their
	# loci, and the calibration snapshot with the qdmi.fomac.v1 metrics.
	qubits = ["QB1", "QB2", "QB3"]
	device = {
		"schema": "qhw-device-v1",
		"provider": "iqm",
		"device": {"id": "ornl-3q", "num_qubits": 3,
			   "technology": "superconducting"},
		"qubits": [
			{"id": qubit, "index": index}
			for index, qubit in enumerate(qubits)],
		"metadata": {"source": "qdmi", "via": "mqt.core.qdmi"},
	}
	info = {
		"backend": "iqm",
		"metadata_supported": True,
		"static_architecture": {},
		"active_qubits": qubits,
		"calibration_set_id": "cal-set-1",
		"qhw_device": device,
	}
	single = [[qubit] for qubit in qubits]
	coupling = {
		"schema": "qhw-coupling-v1",
		"provider": "iqm",
		"device": {"id": "ornl-3q", "num_qubits": 3},
		"coupling": {"directed": False, "nodes": qubits,
			     "edges": [["QB1", "QB2"], ["QB2", "QB3"]]},
		"operations": [
			{"name": "barrier", "arity": 0, "supported_loci": [[]]},
			{"name": "cz", "arity": 2,
			 "supported_loci": [["QB1", "QB2"], ["QB2", "QB3"]]},
			{"name": "measure", "arity": 1, "supported_loci": single},
			{"name": "prx", "arity": 1, "supported_loci": single},
		],
	}
	calibration = {
		"schema": "qhw-calibration-v1",
		"provider": "iqm",
		"device": {"id": "ornl-3q"},
		"calibration": {"calibration_set_id": "cal-set-1"},
		"extensions": {"qdmi.fomac.v1": {
			"duration_unit": "ns",
			"gate_metrics": [
				{"operation": "cz", "locus": ["QB1", "QB2"],
				 "fidelity": 0.995, "duration": 60.0},
				{"operation": "prx", "locus": ["QB1"],
				 "fidelity": 0.9995, "duration": 16.0},
			],
			"qubit_metrics": [{"qubit": "QB1", "t1": 50000.0, "t2": 20000.0}],
		}},
	}
	return info, device, coupling, calibration


def test_target_through_qdmi_records_with_the_qdmi_library(monkeypatch):
	# A backend set to the shim's QDMI library asks every telemetry call
	# through it and builds the target from the QDMI driver's composite
	# and records: the native gates on their loci, the fomac metrics.
	info, device, coupling, calibration = qdmi_records()
	telemetry = FakeTelemetryAPI(info, device, coupling, calibration)
	backend = _backend(
		monkeypatch, telemetry, provider="shim",
		qpm_properties={"provider": "shim"})
	backend.options.lib = "qdmi"

	target = backend.target

	assert [kwargs["lib"] for _name, kwargs in telemetry.calls] == [
		"qdmi", "qdmi", "qdmi", "qdmi"]
	assert target.num_qubits == 3
	assert target.description == "QFw iqm ornl-3q target"
	assert set(target.operation_names) == {"r", "cz", "measure"}
	assert _qargs(target, "cz") == {(0, 1), (1, 0), (1, 2), (2, 1)}
	assert _qargs(target, "r") == {(0,), (1,), (2,)}
	cz = target["cz"][(0, 1)]
	assert abs(cz.error - 0.005) < 1e-12
	assert abs(cz.duration - 60e-9) < 1e-18
	assert target["cz"][(1, 0)] is cz
	assert target["cz"][(1, 2)] is None
	prx = target["r"][(0,)]
	assert abs(prx.error - 0.0005) < 1e-12
	assert abs(prx.duration - 16e-9) < 1e-18
	assert backend.target_source == "device: QFw iqm ornl-3q target"
