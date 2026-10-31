# Guards the IBM-specific additions introduced in the qhw-ibm branch:
#
#   - num_shots(): canonical key, legacy alias, default fallback
#   - _first_error_line(): extracts the first error-looking line from logs
#   - QrmiDriver._provider(): case-insensitive, defaults to "iqm"
#   - QrmiDriver._target() IBM caching guard: a payload with null
#     configuration or properties must not be cached so the driver retries
#     on the next call once the backend recovers
#   - IBM introspection dispatch: get_device_info / get_coupling_graph /
#     get_calibration_snapshot / get_dynamic_backend_info / get_backend_info
#     each route to the qhw_ibm normalizer for an IBM provider and raise
#     DEFwExecutionError when the target carries no data
#   - is_qpy_circuit_provided(): recognises qpy and qpy+gzip, rejects the rest
#   - svc_qpm.QPM.query() removes openqasm2 from circuit_formats for IBM
#
# Everything works on plain dicts and stubs, so this needs no live device,
# no qrmi install, and no qiskit install.

import importlib
import pathlib
import sys
import types

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
import svc_lib_qpm.drivers.qrmi_driver as qd   # noqa: E402
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _driver(**descriptor):
	"""Return a QrmiDriver whose _target() is replaced by a stub."""
	descriptor.setdefault("provider", "ibm")
	descriptor.setdefault("id", "ibm_torino")
	return QrmiDriver(descriptor)


def _driver_with_target(target_data, **descriptor):
	"""Return a driver whose _target() always returns *target_data*."""
	driver = _driver(**descriptor)
	driver._target = lambda credential=None: target_data
	return driver


def _install_qhw_ibm_stub(monkeypatch, config_out=None, coupling_out=None,
						  calibration_out=None, device_out=None):
	"""Install a minimal qhw_ibm stub that records call arguments."""
	calls = {}

	stub = types.ModuleType("qhw_ibm")

	def normalize_device(data, device_id=None):
		calls.setdefault("normalize_device", []).append((data, device_id))
		return device_out if device_out is not None else {"qubits": [0, 1]}

	def normalize_coupling(data, device_id=None):
		calls.setdefault("normalize_coupling", []).append((data, device_id))
		return coupling_out if coupling_out is not None else {"couplings": []}

	def normalize_calibration(data, device_id=None):
		calls.setdefault("normalize_calibration", []).append((data, device_id))
		return calibration_out if calibration_out is not None else {"calibration": {}}

	stub.normalize_device = normalize_device
	stub.normalize_coupling = normalize_coupling
	stub.normalize_calibration = normalize_calibration

	monkeypatch.setitem(sys.modules, "qhw_ibm", stub)
	return calls


# ===========================================================================
# num_shots
# ===========================================================================

def test_num_shots_uses_num_shots_key():
	assert qd.num_shots({"num_shots": 500}) == 500


def test_num_shots_falls_back_to_shots_alias():
	assert qd.num_shots({"shots": 200}) == 200


def test_num_shots_prefers_num_shots_over_shots():
	assert qd.num_shots({"num_shots": 300, "shots": 100}) == 300


def test_num_shots_uses_default_when_absent():
	assert qd.num_shots({}) == qd.DEFAULT_SHOTS


def test_num_shots_accepts_custom_default():
	assert qd.num_shots({}, default=42) == 42


def test_num_shots_coerces_to_int():
	assert qd.num_shots({"num_shots": "128"}) == 128
	assert isinstance(qd.num_shots({"num_shots": 64}), int)


# ===========================================================================
# _first_error_line
# ===========================================================================

def test_first_error_line_returns_none_for_empty_logs():
	assert qd._first_error_line(None) is None
	assert qd._first_error_line("") is None


def test_first_error_line_finds_error_keyword():
	logs = "INFO: job started\nERROR: backend unavailable\nINFO: retrying"
	assert qd._first_error_line(logs) == "ERROR: backend unavailable"


def test_first_error_line_finds_exception_keyword():
	logs = "starting up\nValueError: bad input\nmore info"
	assert qd._first_error_line(logs) == "ValueError: bad input"


def test_first_error_line_finds_traceback_keyword():
	logs = "Traceback (most recent call last):\n  File x.py\nValueError"
	assert qd._first_error_line(logs) == "Traceback (most recent call last):"


def test_first_error_line_finds_failed_keyword():
	logs = "step 1 ok\nstep 2 failed: timeout\nstep 3"
	assert qd._first_error_line(logs) == "step 2 failed: timeout"


def test_first_error_line_returns_none_when_no_error_found():
	logs = "INFO: all good\nDEBUG: check complete"
	assert qd._first_error_line(logs) is None


def test_first_error_line_is_case_insensitive():
	assert qd._first_error_line("FATAL: disk full") == "FATAL: disk full"


def test_first_error_line_strips_surrounding_whitespace():
	logs = "  \n  \n   ERROR: bad state  \n"
	assert qd._first_error_line(logs) == "ERROR: bad state"


# ===========================================================================
# _provider
# ===========================================================================

def test_provider_defaults_to_iqm():
	assert QrmiDriver({})._provider() == "iqm"


def test_provider_returns_ibm_lowercase():
	assert QrmiDriver({"provider": "IBM"})._provider() == "ibm"
	assert QrmiDriver({"provider": "ibm"})._provider() == "ibm"


def test_provider_handles_none_descriptor_value():
	assert QrmiDriver({"provider": None})._provider() == "iqm"


def test_provider_returns_iqm():
	assert QrmiDriver({"provider": "IQM"})._provider() == "iqm"


# ===========================================================================
# _target() IBM caching guard
# ===========================================================================

class _TargetValue:
	def __init__(self, data):
		import json
		self.value = json.dumps(data)


def _qpu_returning(data):
	"""Return a minimal _qpu stub whose target() returns *data*."""
	class _Qpu:
		def target(self_inner):
			return _TargetValue(data)
	return _Qpu()


def test_target_caches_complete_ibm_payload():
	driver = _driver(provider="ibm")
	payload = {"configuration": {"n_qubits": 5}, "properties": {"qubits": []}}
	driver._qpu = lambda credential=None: _qpu_returning(payload)

	result = driver._target()
	assert result == payload
	# Second call must come from the cache (no second _qpu call needed).
	driver._qpu = lambda credential=None: (_ for _ in ()).throw(
		RuntimeError("_qpu called twice"))
	assert driver._target() == payload


def test_target_does_not_cache_ibm_payload_with_null_configuration():
	driver = _driver(provider="ibm")
	call_count = [0]

	def _qpu(credential=None):
		call_count[0] += 1
		return _qpu_returning({"configuration": None, "properties": {"qubits": []}})

	driver._qpu = _qpu

	r1 = driver._target()
	r2 = driver._target()
	assert r1["configuration"] is None
	assert call_count[0] == 2, "incomplete IBM payload must not be cached"


def test_target_does_not_cache_ibm_payload_with_null_properties():
	driver = _driver(provider="ibm")
	call_count = [0]

	def _qpu(credential=None):
		call_count[0] += 1
		return _qpu_returning({"configuration": {"n_qubits": 5}, "properties": None})

	driver._qpu = _qpu

	driver._target()
	driver._target()
	assert call_count[0] == 2, "incomplete IBM payload must not be cached"


def test_target_caches_iqm_payload_regardless_of_content():
	driver = _driver(provider="iqm")
	call_count = [0]

	def _qpu(credential=None):
		call_count[0] += 1
		return _qpu_returning({"dynamic_quantum_architecture": {}})

	driver._qpu = _qpu

	driver._target()
	driver._target()
	assert call_count[0] == 1, "IQM payload must be cached even without IBM keys"


def _counting_target_driver(monkeypatch, ttl=None):
	if ttl is None:
		monkeypatch.delenv(qd.TARGET_TTL_ENV, raising=False)
	else:
		monkeypatch.setenv(qd.TARGET_TTL_ENV, ttl)
	driver = _driver(provider="iqm")
	call_count = [0]

	def _qpu(credential=None):
		call_count[0] += 1
		return _qpu_returning({"dynamic_quantum_architecture": {}})

	driver._qpu = _qpu
	return driver, call_count


def test_target_is_read_again_once_its_entry_expires(monkeypatch):
	# The ("default",) entry is never evicted, so without an expiry a
	# long-running service describes the calibration it first saw for good.
	driver, call_count = _counting_target_driver(monkeypatch)
	clock = [1000.0]
	monkeypatch.setattr(qd.time, "monotonic", lambda: clock[0])

	driver._target()
	clock[0] += qd.DEFAULT_TARGET_TTL_SECONDS - 1
	driver._target()
	assert call_count[0] == 1

	clock[0] += 1
	driver._target()
	assert call_count[0] == 2


def test_target_ttl_of_zero_reads_the_device_every_time(monkeypatch):
	driver, call_count = _counting_target_driver(monkeypatch, ttl="0")

	driver._target()
	driver._target()

	assert call_count[0] == 2


def _expired_driver(monkeypatch, provider, first, refresh):
	# A driver holding an expired entry, whose next read does what refresh says.
	monkeypatch.delenv(qd.TARGET_TTL_ENV, raising=False)
	driver = _driver(provider=provider)
	clock = [1000.0]
	monkeypatch.setattr(qd.time, "monotonic", lambda: clock[0])
	driver._qpu = lambda credential=None: _qpu_returning(first)
	driver._target()
	clock[0] += qd.DEFAULT_TARGET_TTL_SECONDS
	driver._qpu = refresh
	return driver


def test_a_failed_refresh_keeps_the_last_good_target(monkeypatch, caplog):
	# Before the expiry a job never reread the device, so an outage during a
	# refresh must not fail a job that would have run.
	good = {"dynamic_quantum_architecture": {"qubits": ["QB1"]}}

	def refresh(credential=None):
		raise RuntimeError("device unreachable")

	driver = _expired_driver(monkeypatch, "iqm", good, refresh)

	with caplog.at_level("WARNING"):
		assert driver._target() == good
	assert "device unreachable" in caplog.text


def test_an_incomplete_ibm_refresh_keeps_the_last_good_target(monkeypatch):
	good = {"configuration": {"n_qubits": 5}, "properties": {"qubits": []}}
	partial = {"configuration": {"n_qubits": 5}, "properties": None}
	driver = _expired_driver(
		monkeypatch, "ibm", good,
		lambda credential=None: _qpu_returning(partial))

	assert driver._target() == good


def test_a_refresh_after_a_failure_replaces_the_target(monkeypatch):
	good = {"dynamic_quantum_architecture": {"qubits": ["QB1"]}}
	newer = {"dynamic_quantum_architecture": {"qubits": ["QB2"]}}

	def refresh(credential=None):
		raise RuntimeError("device unreachable")

	driver = _expired_driver(monkeypatch, "iqm", good, refresh)
	driver._target()
	driver._qpu = lambda credential=None: _qpu_returning(newer)

	assert driver._target() == newer


def test_a_failed_first_read_still_raises(monkeypatch):
	# With nothing cached there is no target to keep.
	monkeypatch.delenv(qd.TARGET_TTL_ENV, raising=False)
	driver = _driver(provider="iqm")

	def refused(credential=None):
		raise RuntimeError("device unreachable")

	driver._qpu = refused

	with pytest.raises(DEFwExecutionError, match="target"):
		driver._target()


def test_target_ttl_must_be_a_whole_number(monkeypatch):
	monkeypatch.setenv(qd.TARGET_TTL_ENV, "soon")

	with pytest.raises(DEFwExecutionError, match=qd.TARGET_TTL_ENV):
		qd.target_ttl_seconds()


# ===========================================================================
# IBM introspection dispatch
# ===========================================================================

_CONFIG = {"n_qubits": 3, "backend_name": "ibm_torino"}
_PROPS = {"qubits": [], "gates": [], "last_update_date": "2024-01-01"}
_FULL_TARGET = {"configuration": _CONFIG, "properties": _PROPS}


def test_get_device_info_ibm_calls_normalize_device(monkeypatch):
	calls = _install_qhw_ibm_stub(monkeypatch)
	driver = _driver_with_target(_FULL_TARGET)

	result = driver.get_device_info()

	assert "normalize_device" in calls
	data_arg, device_id_arg = calls["normalize_device"][0]
	assert data_arg == _CONFIG
	assert device_id_arg == "ibm_torino"
	assert result == {"qubits": [0, 1]}


def test_get_device_info_ibm_raises_on_empty_configuration(monkeypatch):
	_install_qhw_ibm_stub(monkeypatch)
	driver = _driver_with_target({"configuration": {}, "properties": _PROPS})

	with pytest.raises(DEFwExecutionError, match="QRMI failed to retrieve"):
		driver.get_device_info()


def test_get_coupling_graph_ibm_calls_normalize_coupling(monkeypatch):
	calls = _install_qhw_ibm_stub(monkeypatch)
	driver = _driver_with_target(_FULL_TARGET)

	result = driver.get_coupling_graph()

	assert "normalize_coupling" in calls
	data_arg, _ = calls["normalize_coupling"][0]
	assert data_arg == _CONFIG
	assert result == {"couplings": []}


def test_get_coupling_graph_ibm_raises_on_empty_configuration(monkeypatch):
	_install_qhw_ibm_stub(monkeypatch)
	driver = _driver_with_target({"configuration": None, "properties": _PROPS})

	with pytest.raises(DEFwExecutionError, match="QRMI failed to retrieve"):
		driver.get_coupling_graph()


def test_get_calibration_snapshot_ibm_calls_normalize_calibration(monkeypatch):
	calls = _install_qhw_ibm_stub(monkeypatch)
	driver = _driver_with_target(_FULL_TARGET)

	result = driver.get_calibration_snapshot()

	assert "normalize_calibration" in calls
	data_arg, _ = calls["normalize_calibration"][0]
	assert data_arg == _PROPS
	assert result == {"calibration": {}}


def test_get_calibration_snapshot_ibm_raises_on_empty_properties(monkeypatch):
	_install_qhw_ibm_stub(monkeypatch)
	driver = _driver_with_target({"configuration": _CONFIG, "properties": {}})

	with pytest.raises(DEFwExecutionError, match="QRMI failed to retrieve"):
		driver.get_calibration_snapshot()


def test_get_dynamic_backend_info_ibm_returns_static_shape():
	driver = _driver_with_target(_FULL_TARGET)

	result = driver.get_dynamic_backend_info()

	assert result["backend"] == "ibm"
	assert result["metadata_supported"] is False
	assert result["dynamic_architecture"] == {}


def test_get_backend_info_ibm_returns_composite_shape(monkeypatch):
	_install_qhw_ibm_stub(monkeypatch, device_out={"qubits": [0, 1, 2]})
	driver = _driver_with_target(dict(_FULL_TARGET, **{}))
	# Patch the config to carry n_qubits explicitly.
	config = dict(_CONFIG, n_qubits=3)
	driver._target = lambda credential=None: {
		"configuration": config, "properties": _PROPS}

	result = driver.get_backend_info()

	assert result["backend"] == "ibm"
	assert result["metadata_supported"] is True
	assert result["static_architecture"] == config
	# The raw properties ride along, so a client can build the same ISA
	# target _build_backend_target does.
	assert result["backend_properties"] == _PROPS
	assert result["active_qubits"] == [0, 1, 2]
	assert result["calibration_set_id"] is None
	assert result["qhw_device"] == {"qubits": [0, 1, 2]}


def test_get_backend_info_ibm_falls_back_to_qhw_device_qubit_count(monkeypatch):
	# When configuration carries no n_qubits the length of qhw_device["qubits"]
	# is used as the active qubit count.
	_install_qhw_ibm_stub(monkeypatch, device_out={"qubits": ["Q0", "Q1"]})
	config = {"backend_name": "ibm_sherbrooke"}  # no n_qubits key
	driver = _driver_with_target({"configuration": config, "properties": _PROPS})

	result = driver.get_backend_info()

	assert result["active_qubits"] == [0, 1]


def test_get_backend_info_ibm_raises_on_empty_configuration(monkeypatch):
	_install_qhw_ibm_stub(monkeypatch)
	driver = _driver_with_target({"configuration": {}, "properties": _PROPS})

	with pytest.raises(DEFwExecutionError, match="QRMI failed to retrieve"):
		driver.get_backend_info()


@pytest.mark.parametrize("provider,target", (
	("ibm", _FULL_TARGET),
	("iqm", {"dynamic_quantum_architecture": {"qubits": ["QB1"]}}),
))
def test_get_backend_info_reads_the_target_with_the_credential(
		monkeypatch, provider, target):
	# A reservation-bound read opens the device with the reservation's
	# credential on every provider, as it does for Pasqal.
	_install_qhw_ibm_stub(monkeypatch)
	iqm_stub = types.ModuleType("qhw_iqm")
	iqm_stub.normalize_device = lambda *a, **k: {}
	monkeypatch.setitem(sys.modules, "qhw_iqm", iqm_stub)
	driver = _driver(provider=provider)
	credentials = []
	driver._target = lambda credential=None: (
		credentials.append(credential) or target)
	credential = {"reservation_id": 7, "token": "user-token"}

	driver.get_backend_info(credential=credential)

	assert credentials and all(c == credential for c in credentials)


# Introspection methods must route to qhw_iqm (not qhw_ibm) for non-IBM.
def test_get_dynamic_backend_info_iqm_returns_live_architecture(monkeypatch):
	iqm_stub = types.ModuleType("qhw_iqm")
	iqm_stub.normalize_device = lambda *a, **k: {}
	monkeypatch.setitem(sys.modules, "qhw_iqm", iqm_stub)

	iqm_target = {"dynamic_quantum_architecture": {"qubits": ["QB1", "QB2"]}}
	driver = QrmiDriver({"provider": "iqm", "id": "my-iqm"})
	driver._target = lambda credential=None: iqm_target

	result = driver.get_dynamic_backend_info()

	assert result["backend"] == "iqm"
	assert result["metadata_supported"] is True
	assert result["dynamic_architecture"] == {"qubits": ["QB1", "QB2"]}


# ===========================================================================
# is_qpy_circuit_provided
# ===========================================================================

from util.circuit_payload import is_qpy_circuit_provided  # noqa: E402


def test_is_qpy_circuit_provided_true_for_qpy():
	assert is_qpy_circuit_provided({"circuit": {"format": "qpy", "data": "x"}})


def test_is_qpy_circuit_provided_true_for_qpy_gzip():
	assert is_qpy_circuit_provided({"circuit": {"format": "qpy+gzip", "data": "x"}})


def test_is_qpy_circuit_provided_false_for_openqasm2():
	assert not is_qpy_circuit_provided({"circuit": {"format": "openqasm2", "data": "x"}})


def test_is_qpy_circuit_provided_false_when_format_absent():
	assert not is_qpy_circuit_provided({"circuit": {}})


def test_is_qpy_circuit_provided_false_when_circuit_absent():
	assert not is_qpy_circuit_provided({})


def test_is_qpy_circuit_provided_false_for_none_info():
	assert not is_qpy_circuit_provided(None)


def test_is_qpy_circuit_provided_is_case_insensitive():
	assert is_qpy_circuit_provided({"circuit": {"format": "QPY", "data": "x"}})


# ===========================================================================
# svc_qpm.QPM.query() — IBM strips openqasm2 from circuit_formats
# ===========================================================================

def _fake_qiskit_with_version(monkeypatch, version=13):
	"""Install a minimal qiskit stub so qiskit_circuit_formats() returns formats."""
	qiskit_stub = types.ModuleType("qiskit")
	qiskit_stub.__version__ = f"{version}.0.0"
	qpy_stub = types.ModuleType("qiskit.qpy")
	qpy_stub.QPY_VERSION = version
	monkeypatch.setitem(sys.modules, "qiskit", qiskit_stub)
	monkeypatch.setitem(sys.modules, "qiskit.qpy", qpy_stub)


def _make_qpm_stub(monkeypatch, provider):
	"""Return a QPM instance whose query() can run without a live service."""
	import svc_lib_qpm
	from svc_lib_qpm.svc_qpm import QPM
	import util.device_access as device_access

	monkeypatch.delenv("QFW_QPU_DEVICE_ID", raising=False)
	_fake_qiskit_with_version(monkeypatch)

	device = {
		"provider": provider,
		"provider-device-id": "dev",
		"url": "https://example.org/",
		"credential-db": "creds.json",
	}
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: {"qpus": {"dev": device}})
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	qpm = QPM.__new__(QPM)
	qpm.controller_telemetry = lambda: {}
	return qpm


def test_query_ibm_removes_openqasm2_from_circuit_formats(monkeypatch):
	qpm = _make_qpm_stub(monkeypatch, provider="ibm")
	formats = qpm.query()["properties"]["circuit_formats"]
	assert "openqasm2" not in formats
	# QPY formats must still be present.
	assert "qpy" in formats


def test_query_iqm_keeps_openqasm2_in_circuit_formats(monkeypatch):
	qpm = _make_qpm_stub(monkeypatch, provider="iqm")
	formats = qpm.query()["properties"]["circuit_formats"]
	assert "openqasm2" in formats
