# QFwBackend.compile: what it does with what a profile returns. The profiles
# themselves need real Qiskit and are in tests/qiskit/test_compile_profiles.py.

import pytest

from defw_exception import DEFwError, DEFwExecutionError
from qiskit import QuantumCircuit
from qfw_qiskit import qfw_metadata
from tests.mock import test_qfw_backend_target as target_tests

DEVICE = "q20"
LABELS = ["QB1", "QB2"]
CALIBRATION = {"kind": "set-id", "value": "cal-1"}
PUBLISHED = {"isa_provider": "iqm", "isa_device_id": DEVICE}


class Circuit(QuantumCircuit):
	def __init__(self):
		super().__init__(2)
		self.metadata = {}

	def copy(self):
		duplicate = Circuit()
		duplicate.metadata = dict(self.metadata)
		return duplicate


class Profile:
	compiles_against_target = False

	def __init__(self):
		self.compiled = Circuit()
		self.validated = []
		self.options = []

	def read_device(self, backend):
		return {"device": True}

	def compile(self, circuit, device, mapping=None, **options):
		self.mapping = mapping
		self.options.append(options)
		return self.compiled, LABELS

	def compile_all(self, circuits, device, mappings, **options):
		self.batches = getattr(self, "batches", 0) + 1
		return [
			self.compile(circuit, device, mapping, **options)
			for circuit, mapping in zip(circuits, mappings)]

	def labels_for_target(self, device):
		return LABELS

	def validate(self, circuit, device, marker):
		self.validated.append((circuit, marker))

	def calibration(self, device):
		return CALIBRATION


def _backend(monkeypatch, profile=None, properties=None, telemetry=None):
	import qfw_qiskit.qfw_compile as qfw_compile
	if properties is None:
		properties = PUBLISHED
	backend = target_tests._backend(
		monkeypatch, telemetry, qpm_properties=dict(properties))
	if profile is not None:
		monkeypatch.setattr(
			qfw_compile, "compile_profile", lambda provider: profile)
	return backend


def test_a_compiled_circuit_is_marked_for_the_device_its_qpm_publishes(
		monkeypatch):
	backend = _backend(monkeypatch, Profile())

	compiled = backend.compile(Circuit())

	marker = qfw_metadata.get_isa_marker(compiled)
	assert (marker["provider"], marker["device_id"]) == ("iqm", DEVICE)
	assert marker["calibration"] == CALIBRATION
	assert marker["qubit_labels"] == LABELS


def test_the_compiled_circuit_is_checked_before_it_is_marked(monkeypatch):
	profile = Profile()
	backend = _backend(monkeypatch, profile)

	def refuse(circuit, device, marker):
		raise DEFwExecutionError("not native")

	profile.validate = refuse

	with pytest.raises(DEFwExecutionError, match="not native"):
		backend.compile(Circuit())
	assert qfw_metadata.get_isa_marker(profile.compiled) is None


def test_the_check_sees_the_marker_the_circuit_will_carry(monkeypatch):
	profile = Profile()
	backend = _backend(monkeypatch, profile)

	compiled = backend.compile(Circuit())

	assert profile.validated[0][1] == qfw_metadata.get_isa_marker(compiled)


def test_compiling_replaces_a_stale_qubit_mapping(monkeypatch):
	profile = Profile()
	qfw_metadata.set_qubit_mapping(profile.compiled, ["QB8", "QB9"])
	backend = _backend(monkeypatch, profile)

	compiled = backend.compile(Circuit())

	assert qfw_metadata.get_qubit_mapping(compiled) == {"0": "QB1", "1": "QB2"}


def test_a_circuit_the_compiler_left_alone_is_not_marked_in_place(monkeypatch):
	profile = Profile()
	circuit = Circuit()
	profile.compile = lambda circuit, device, mapping=None, **options: (
		circuit, None)
	backend = _backend(monkeypatch, profile)

	backend.compile(circuit)

	assert qfw_metadata.get_isa_marker(circuit) is None


def test_the_input_mapping_and_options_reach_the_profile(monkeypatch):
	profile = Profile()
	backend = _backend(monkeypatch, profile)
	circuit = Circuit()
	qfw_metadata.set_qubit_mapping(circuit, ["QB2", "QB1"])

	backend.compile(circuit, seed_transpiler=7)

	assert profile.mapping == {"0": "QB2", "1": "QB1"}
	assert profile.options == [{"seed_transpiler": 7}]


def test_a_list_comes_back_a_list(monkeypatch):
	profile = Profile()
	backend = _backend(monkeypatch, profile)

	compiled = backend.compile([Circuit(), Circuit()])

	assert len(compiled) == 2
	assert all(qfw_metadata.get_isa_marker(circuit) for circuit in compiled)
	assert profile.batches == 1


def test_a_qpm_that_takes_no_marker_gets_an_unmarked_circuit(monkeypatch, caplog):
	profile = Profile()
	backend = _backend(monkeypatch, profile, {"provider": "iqm"})

	with caplog.at_level("WARNING"):
		compiled = backend.compile(Circuit())

	assert qfw_metadata.get_isa_marker(compiled) is None
	assert len(profile.validated) == 1
	assert "compile this one again" in caplog.text


def test_a_compiler_result_that_cannot_be_checked_is_refused(monkeypatch):
	profile = Profile()
	profile.compile = lambda *args, **kwargs: ("mlir", None)
	backend = _backend(monkeypatch, profile)

	with pytest.raises(DEFwError, match="cannot check"):
		backend.compile(Circuit())


def test_mqt_output_takes_the_labels_of_the_target_it_compiled_to(monkeypatch):
	backend = _backend(monkeypatch, Profile())
	monkeypatch.setattr(
		type(backend), "_compile_with_mqt",
		lambda self, circuit, **options: Circuit())

	compiled = backend.compile(Circuit(), "mqt")

	assert qfw_metadata.get_isa_marker(compiled)["qubit_labels"] == LABELS


def test_an_unknown_compiler_is_refused(monkeypatch):
	backend = _backend(monkeypatch, Profile())

	with pytest.raises(ValueError, match="unknown compiler"):
		backend.compile(Circuit(), "tket")


@pytest.mark.parametrize("properties", [
	{"device_provider": "aws"}, {"provider": "backline"},
])
def test_a_device_that_compiles_itself_is_refused(monkeypatch, properties):
	backend = _backend(monkeypatch, properties=properties)

	with pytest.raises(DEFwExecutionError, match="not compiled ahead of time"):
		backend.compile(Circuit())


def test_a_simulator_compiles_to_its_generic_target_unmarked(monkeypatch):
	backend = _backend(monkeypatch, properties={"provider": "nwqsim"})
	seen = []
	monkeypatch.setitem(
		backend.COMPILERS, "qiskit",
		lambda self, circuits, **options: seen.append(circuits) or "compiled")
	circuit = Circuit()

	assert backend.compile(circuit) == "compiled"
	assert seen == [circuit]


def test_a_device_its_qpm_cannot_describe_compiles_to_the_target_unmarked(
		monkeypatch, caplog):
	from util.compile_profiles import DeviceNotDescribed
	profile = Profile()

	def not_described(backend):
		raise DeviceNotDescribed("no dynamic architecture")

	profile.read_device = not_described
	backend = _backend(monkeypatch, profile)
	monkeypatch.setattr(backend, "_require_a_device_target", lambda: None)
	monkeypatch.setitem(
		backend.COMPILERS, "qiskit",
		lambda self, circuits, **options: "compiled")

	with caplog.at_level("WARNING"):
		assert backend.compile(Circuit()) == "compiled"
	assert "compile it again" in caplog.text


def test_check_isa_refuses_a_device_its_qpm_cannot_describe(monkeypatch):
	from util.compile_profiles import DeviceNotDescribed
	profile = Profile()

	def not_described(backend):
		raise DeviceNotDescribed("no dynamic architecture")

	profile.read_device = not_described
	backend = _backend(
		monkeypatch, profile, dict(PUBLISHED, circuit_stages=["isa"]))

	with pytest.raises(DeviceNotDescribed):
		backend.check_isa(Circuit())


def test_a_fallback_target_is_never_compiled_to(monkeypatch):
	backend = _backend(
		monkeypatch, properties={"provider": "qb", "hardware": True})

	with pytest.raises(DEFwError, match="no telemetry binding"):
		backend.compile(Circuit())


def test_a_target_profile_refuses_a_fallback_target(monkeypatch):
	profile = Profile()
	profile.compiles_against_target = True
	backend = _backend(monkeypatch, profile, dict(PUBLISHED, hardware=True))

	with pytest.raises(DEFwError, match="connectivity"):
		backend.compile(Circuit())


def test_refresh_target_reads_the_device_again(monkeypatch):
	telemetry = target_tests.FakeTelemetryAPI(
		backend_info={"backend": "nwqsim", "metadata_supported": False})
	backend = _backend(
		monkeypatch, Profile(), telemetry=telemetry)
	backend.target
	reads = telemetry.names().count("get_backend_info")

	backend._read_compile_device = lambda profile: backend.target or {}
	backend.compile(Circuit(), refresh_target=True)

	assert telemetry.names().count("get_backend_info") == reads + 1


def test_refresh_target_keeps_a_target_the_caller_supplied(monkeypatch):
	backend = _backend(monkeypatch, Profile())
	backend._target_supplied = True
	backend._target = "supplied"

	backend.compile(Circuit(), refresh_target=True)

	assert backend._target == "supplied"


# --- check_isa ---------------------------------------------------------------

STAGES = {"circuit_stages": ["generic", "isa"]}


def _marked(calibration=CALIBRATION, device=DEVICE):
	circuit = Circuit()
	return qfw_metadata.mark_isa(
		circuit, "iqm", device, calibration=calibration, qubit_labels=LABELS)


def _checking_backend(monkeypatch, profile, **properties):
	return _backend(monkeypatch, profile, dict(PUBLISHED, **STAGES, **properties))


def test_check_isa_passes_a_circuit_the_device_would_run(monkeypatch):
	profile = Profile()
	circuit = _marked()

	_checking_backend(monkeypatch, profile).check_isa(circuit)

	assert profile.validated[0][0] is circuit


def test_check_isa_reads_the_device_again(monkeypatch):
	telemetry = target_tests.FakeTelemetryAPI(
		backend_info={"backend": "nwqsim", "metadata_supported": False})
	profile = Profile()
	profile.compiles_against_target = True
	profile.read_device = lambda backend: backend.target and {}
	backend = _backend(
		monkeypatch, profile, dict(PUBLISHED, **STAGES), telemetry)
	backend.target
	reads = telemetry.names().count("get_backend_info")

	backend.check_isa(_marked())

	assert telemetry.names().count("get_backend_info") == reads + 1


def _device_backend(monkeypatch, profile):
	telemetry = target_tests.FakeTelemetryAPI(*target_tests.iqm_records())
	backend = _backend(
		monkeypatch, profile, dict(PUBLISHED, **STAGES, hardware=True),
		telemetry)
	assert backend.target_kind is None and backend.target is not None
	assert backend.target_kind == "device"
	telemetry.failures["get_backend_info"] = RuntimeError("QPM restarting")
	return backend


def test_a_failed_check_keeps_the_device_target(monkeypatch):
	profile = Profile()
	profile.compiles_against_target = True
	backend = _device_backend(monkeypatch, profile)
	target = backend.target

	with pytest.raises(DEFwError, match="QPM restarting"):
		backend.check_isa(_marked())

	assert backend.target is target
	assert backend.target_kind == "device"


def test_a_failed_refresh_keeps_the_device_target(monkeypatch):
	backend = _device_backend(monkeypatch, Profile())
	target = backend.target

	with pytest.raises(DEFwError, match="last read"):
		backend.compile(Circuit(), refresh_target=True)

	assert backend.target is target
	assert backend.target_kind == "device"


def test_check_isa_takes_a_list(monkeypatch):
	profile = Profile()

	_checking_backend(monkeypatch, profile).check_isa([_marked(), _marked()])

	assert len(profile.validated) == 2


def test_check_isa_refuses_an_unmarked_circuit(monkeypatch):
	with pytest.raises(DEFwError, match="not marked"):
		_checking_backend(monkeypatch, Profile()).check_isa(Circuit())


def test_check_isa_refuses_another_device(monkeypatch):
	with pytest.raises(DEFwExecutionError, match="'q54'"):
		_checking_backend(monkeypatch, Profile()).check_isa(_marked(device="q54"))


def test_check_isa_refuses_a_stale_calibration(monkeypatch):
	stale = {"kind": "set-id", "value": "cal-0"}

	with pytest.raises(DEFwExecutionError, match="compile it again"):
		_checking_backend(monkeypatch, Profile()).check_isa(_marked(stale))


def test_check_isa_honours_the_policy_the_qpm_publishes(monkeypatch):
	stale = {"kind": "set-id", "value": "cal-0"}
	backend = _checking_backend(
		monkeypatch, Profile(), calibration_policy="current")

	backend.check_isa(_marked(stale))


def test_check_isa_refuses_what_the_device_would(monkeypatch):
	profile = Profile()

	def refuse(circuit, device, marker):
		raise DEFwExecutionError("not native")

	profile.validate = refuse

	with pytest.raises(DEFwExecutionError, match="not native"):
		_checking_backend(monkeypatch, profile).check_isa(_marked())


def test_check_isa_needs_a_qpm_that_takes_a_marker(monkeypatch):
	backend = _backend(monkeypatch, Profile(), {"provider": "nwqsim"})

	with pytest.raises(DEFwError, match="compiled ahead of time"):
		backend.check_isa(_marked())


# --- device_snapshot ---------------------------------------------------------

SNAPSHOT_RECORDS = target_tests.iqm_records()
DYNAMIC = {"dynamic_architecture": {"qubits": target_tests.IQM_QUBITS}}
SERVES_DYNAMIC = dict(PUBLISHED, dynamic_architecture=True)


def _snapshot_telemetry(**fields):
	telemetry = target_tests.FakeTelemetryAPI(*SNAPSHOT_RECORDS, **fields)
	telemetry.records["get_dynamic_backend_info"] = DYNAMIC
	return telemetry


def _snapshot_backend(monkeypatch, properties=SERVES_DYNAMIC, **fields):
	return _backend(
		monkeypatch, properties=properties,
		telemetry=_snapshot_telemetry(**fields))


def test_a_snapshot_holds_the_device_records(monkeypatch):
	backend = _snapshot_backend(monkeypatch)

	snapshot = backend.device_snapshot()

	info, device, coupling, calibration = SNAPSHOT_RECORDS
	assert snapshot["schema"] == "qfw-device-snapshot-v1"
	assert (snapshot["provider"], snapshot["device_id"]) == ("iqm", DEVICE)
	assert snapshot["captured_at"].endswith("+00:00")
	assert snapshot["records"] == {
		"get_backend_info": info, "get_device_info": device,
		"get_coupling_graph": coupling, "get_calibration_snapshot": calibration,
		"get_dynamic_backend_info": DYNAMIC}
	assert snapshot["published"] == SERVES_DYNAMIC


def test_a_snapshot_of_another_schema_is_refused():
	from qfw_qiskit.qfw_compile import SnapshotDevice

	with pytest.raises(DEFwExecutionError, match="qfw-device-snapshot-v1"):
		SnapshotDevice({"schema": "something-else"})


def test_a_snapshot_cannot_read_its_device_again(monkeypatch):
	from qfw_qiskit.qfw_compile import SnapshotDevice
	snapshot = _snapshot_backend(monkeypatch).device_snapshot()

	with pytest.raises(DEFwExecutionError, match="take a new one"):
		SnapshotDevice(snapshot).compile(Circuit(), refresh_target=True)


def test_a_snapshot_answers_for_the_qpm_it_recorded(monkeypatch):
	from qfw_qiskit.qfw_compile import SnapshotDevice
	snapshot = _snapshot_backend(monkeypatch).device_snapshot()

	device = SnapshotDevice(snapshot)

	assert device.isa_device() == ("iqm", DEVICE)
	assert device.telemetry.get_dynamic_backend_info() == DYNAMIC


def test_a_snapshot_goes_without_a_record_a_compile_does_not_read(monkeypatch):
	backend = _snapshot_backend(monkeypatch, failures={
		"get_coupling_graph": RuntimeError("not implemented")})

	assert backend.device_snapshot()["records"]["get_coupling_graph"] is None


def test_a_snapshot_without_the_record_a_compile_reads_is_refused(monkeypatch):
	backend = _snapshot_backend(monkeypatch, failures={
		"get_dynamic_backend_info": RuntimeError("device unreachable")})

	with pytest.raises(DEFwError, match="take it again"):
		backend.device_snapshot()


def test_a_snapshot_of_records_no_target_can_be_built_from_is_refused(
		monkeypatch):
	telemetry = target_tests.FakeTelemetryAPI(
		backend_info={"backend": "iqm", "metadata_supported": True},
		device_info={"device": 1})
	backend = _backend(monkeypatch, properties=PUBLISHED, telemetry=telemetry)

	with pytest.raises(DEFwError, match="take it again"):
		backend.device_snapshot()


def test_a_device_that_is_not_compiled_ahead_of_time_still_snapshots(
		monkeypatch):
	backend = _snapshot_backend(
		monkeypatch, properties={"device_provider": "aws"})

	assert backend.device_snapshot()["provider"] == "aws"


def test_a_snapshot_needs_a_device_to_describe(monkeypatch):
	telemetry = target_tests.FakeTelemetryAPI(
		backend_info={"backend": "nwqsim", "metadata_supported": False})
	backend = _backend(monkeypatch, telemetry=telemetry)

	with pytest.raises(DEFwError, match="does not describe a device"):
		backend.device_snapshot()


def test_a_snapshot_needs_the_telemetry_binding(monkeypatch):
	with pytest.raises(DEFwError, match="no telemetry binding"):
		_backend(monkeypatch).device_snapshot()
