# The QDMI driver serves get_backend_info, the call the Qiskit client leads
# with, so a QDMI-only resource (a Braket entry) describes its device instead
# of leaving the client on the generic target. The composite is the one the
# QRMI driver returns for a device that is not IBM.
import pathlib
import sys
import types

import pytest

from tests.mock.fakes import stub_qdmi_session


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from svc_lib_qpm.descriptor import DEFAULT_CAPS  # noqa: E402
from svc_lib_qpm.drivers import qdmi_normalize  # noqa: E402
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver  # noqa: E402
from svc_lib_qpm.frontend import Frontend, NotImplementedByLibrary  # noqa: E402


def _module(name, **attrs):
	module = types.ModuleType(name)
	for key, value in attrs.items():
		setattr(module, key, value)
	return module


def _install_custom_property(monkeypatch):
	monkeypatch.setitem(sys.modules, "mqt", _module("mqt"))
	monkeypatch.setitem(sys.modules, "mqt.core", _module("mqt.core"))
	monkeypatch.setitem(sys.modules, "mqt.core.qdmi", _module(
		"mqt.core.qdmi",
		CustomProperty=types.SimpleNamespace(CUSTOM1="slot-1")))


def _device(calibration_set="cal-set-1"):
	# Three sites in a line, IQM's native operations on them, the
	# calibration set id in the custom slot the IQM profile names.
	sites = [types.SimpleNamespace(name=lambda label=label: label)
		 for label in ("QB1", "QB2", "QB3")]
	prx = types.SimpleNamespace(
		name=lambda: "prx", qubits_num=lambda: 1, sites=lambda: sites)
	cz = types.SimpleNamespace(
		name=lambda: "cz", qubits_num=lambda: 2,
		site_pairs=lambda: [(sites[0], sites[1]), (sites[1], sites[2])])
	measure = types.SimpleNamespace(
		name=lambda: "measure", qubits_num=lambda: 1, sites=lambda: sites)
	device = types.SimpleNamespace(
		regular_sites=lambda: sites,
		operations=lambda: [prx, cz, measure],
		duration_unit=lambda: "us",
		queries=[])

	def query_custom_property(slot, value_type):
		device.queries.append((slot, value_type))
		return calibration_set
	device.query_custom_property = query_custom_property
	return device


def _driver(device):
	driver = QdmiDriver({
		"provider": "iqm", "id": "ornl-iqm-20q",
		"provider_device_id": "default"})
	stub_qdmi_session(driver, device)
	return driver


def test_the_qdmi_driver_serves_get_backend_info():
	assert "get_backend_info" in QdmiDriver.CAPABILITIES
	assert _driver(_device()).implements("get_backend_info")


def test_get_backend_info_is_the_qrmi_composite_from_the_topology(monkeypatch):
	_install_custom_property(monkeypatch)
	device = _device()
	records = []

	def to_device_record(topo, provider, device_id, **kwargs):
		records.append((topo, provider, device_id, kwargs))
		return {"schema": "qhw-device-v1", "qubits": topo["qubits"]}

	monkeypatch.setattr(qdmi_normalize, "to_device_record", to_device_record)

	info = _driver(device).get_backend_info()

	assert info == {
		"backend": "iqm",
		"metadata_supported": True,
		"static_architecture": {},
		"active_qubits": ["QB1", "QB2", "QB3"],
		"calibration_set_id": "cal-set-1",
		"qhw_device": {
			"schema": "qhw-device-v1", "qubits": ["QB1", "QB2", "QB3"]},
	}
	topo, provider, device_id, kwargs = records[0]
	assert (provider, device_id) == ("iqm", "ornl-iqm-20q")
	assert topo["edges"] == [["QB1", "QB2"], ["QB2", "QB3"]]
	assert sorted(topo["operations"]) == ["cz", "measure", "prx"]
	assert kwargs == {"technology": "superconducting"}
	# The IQM profile's slot was queried for the id.
	assert device.queries == [("slot-1", str)]


def test_a_provider_without_a_calibration_slot_reports_none(monkeypatch):
	_install_custom_property(monkeypatch)
	monkeypatch.setattr(
		qdmi_normalize, "to_device_record",
		lambda topo, provider, device_id, **kwargs: {"qubits": topo["qubits"]})
	device = _device()
	driver = _driver(device)
	driver._profile.calibration_set_slot = None

	info = driver.get_backend_info()

	assert info["calibration_set_id"] is None
	assert device.queries == []


def test_get_backend_info_embeds_a_real_device_record(monkeypatch):
	pytest.importorskip("qhw_data")
	_install_custom_property(monkeypatch)

	info = _driver(_device()).get_backend_info()

	record = info["qhw_device"]
	assert record["schema"] == "qhw-device-v1"
	assert record["provider"] == "iqm"
	assert record["device"]["id"] == "ornl-iqm-20q"
	assert record["device"]["num_qubits"] == 3
	assert record["device"]["technology"] == "superconducting"
	assert [qubit["id"] for qubit in record["qubits"]] == ["QB1", "QB2", "QB3"]


# --- routing: the shim offers the call on both libraries --------------------

class _Library:
	CAPABILITIES = frozenset({
		"get_backend_info", "get_device_info", "get_coupling_graph",
		"get_calibration_snapshot", "run_circuit"})

	def __init__(self, name):
		self.name = name

	def implements(self, call):
		return call in self.CAPABILITIES

	def get_backend_info(self, credential=None):
		return {"backend": self.name, "metadata_supported": True}


def _frontend(libraries, preference="qdmi"):
	drivers = [_Library(name) for name in libraries]
	return Frontend(drivers, {
		"id": "device",
		"libraries": list(libraries),
		"preference": preference,
		"execution_owner": "qrmi" if "qrmi" in libraries else libraries[0],
		"caps": {call: list(libs) for call, libs in DEFAULT_CAPS.items()},
	})


def test_the_capability_map_offers_get_backend_info_on_both_libraries():
	frontend = _frontend(["qrmi", "qdmi"])

	assert frontend.capability_map()["get_backend_info"] == ["qdmi", "qrmi"]
	assert frontend.get_backend_info(lib="qdmi")["backend"] == "qdmi"
	assert frontend.get_backend_info(lib="qrmi")["backend"] == "qrmi"
	# No library named: the descriptor's preference, as for the other
	# introspection calls.
	assert frontend.get_backend_info()["backend"] == "qdmi"
	assert _frontend(["qrmi", "qdmi"], preference="qrmi") \
		.get_backend_info()["backend"] == "qrmi"


def test_a_qdmi_only_resource_serves_get_backend_info():
	# A Braket entry wires QDMI alone. Before, the call was a gap there.
	frontend = _frontend(["qdmi"])

	assert frontend.capability_map()["get_backend_info"] == ["qdmi"]
	assert frontend.get_backend_info()["backend"] == "qdmi"
	with pytest.raises(NotImplementedByLibrary):
		frontend.get_backend_info(lib="qrmi")


def test_a_reservation_bound_read_opens_the_qdmi_device_with_its_credential(
		monkeypatch):
	# The telemetry API takes a reservation_id on every QPM, and the shim
	# then hands the reservation's credential to whichever library serves
	# the call; QDMI is the default for this one.
	_install_custom_property(monkeypatch)
	monkeypatch.setattr(
		qdmi_normalize, "to_device_record",
		lambda topo, provider, device_id, **kwargs: {"qubits": topo["qubits"]})
	driver = _driver(_device())
	opened = []
	device = driver._device
	driver._device = lambda credential=None: (
		opened.append(credential) or device(credential))
	frontend = Frontend([driver], {
		"id": "ornl-iqm-20q",
		"libraries": ["qdmi"],
		"preference": "qdmi",
		"execution_owner": "qdmi",
		"caps": {call: list(libs) for call, libs in DEFAULT_CAPS.items()},
	})
	credential = {"reservation_id": 7, "token": "user-token"}

	info = frontend.get_backend_info(credential=credential)

	assert info["active_qubits"] == ["QB1", "QB2", "QB3"]
	assert opened == [credential]
