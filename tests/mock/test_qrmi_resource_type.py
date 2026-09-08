# Guards the QRMI resource-type dispatch (openQSE/QFw#59 blocker 1). QRMI
# fronts several vendors and ResourceType selects which backend a
# QuantumResource actually opens. The driver used to hardcode IQMServer, so
# QFw could not drive any other QRMI backend however it was configured.
#
# Three things have to line up before a non-IQM descriptor reaches the driver
# at all, each with its own failure mode, so each is covered here:
#   - select_qpu must forward resource-type; its passthrough is a fixed list
#     and a key missing from it is dropped silently
#   - resolve_descriptor must not force the default provider onto a device
#     that was named, or select_qpu rejects it with "expected 'iqm'"
#   - the driver must take the type from the descriptor
#
# Everything works on plain dicts and a stub qrmi, so this needs no live
# device and no qrmi install.

import importlib.util
import pathlib
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

import util.device_access as device_access  # noqa: E402
from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402


IBM_DEVICE = {
	"provider": "ibm",
	"provider-device-id": "ibm_torino",
	"url": "https://example.org/",
	"credential-db": "creds.json",
	"libraries": ["qrmi"],
	"preference": "qrmi",
	"execution-owner": "qrmi",
	"resource-type": "IBMQiskitRuntimeService",
	"caps": {"get_device_info": ["qrmi"], "run_circuit": ["qrmi"]},
}

IQM_DEVICE = {
	"provider": "iqm",
	"provider-device-id": "default",
	"url": "https://example.org/",
	"credential-db": "creds.json",
}


class _StubResourceType:
	IQMServer = "IQMServer"
	IBMQiskitRuntimeService = "IBMQiskitRuntimeService"
	IBMQuantumComputeService = "IBMQuantumComputeService"
	IBMQuantumSystem = "IBMQuantumSystem"
	PasqalCloud = "PasqalCloud"
	PasqalLocal = "PasqalLocal"


class _StubQrmi:
	ResourceType = _StubResourceType


def _config(device, device_id="dev"):
	return {"qpus": {device_id: device}}


def _load_descriptor():
	# Load descriptor.py directly, bypassing svc_lib_qpm/__init__.py, the same
	# way test_descriptor_config.py does.
	spec = importlib.util.spec_from_file_location(
		"svc_lib_qpm_descriptor_resource_type",
		str(REPO_ROOT / "services" / "svc_lib_qpm" / "descriptor.py"))
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


# --- select_qpu: resource-type has to survive the passthrough --------------

def test_select_qpu_forwards_resource_type(monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(IBM_DEVICE), "cfg.yaml", provider="ibm")
	assert selected["resource-type"] == "IBMQiskitRuntimeService"


def test_select_qpu_forwards_resource_type_underscore(monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	device = dict(IQM_DEVICE, resource_type="IQMServer")
	selected = device_access.select_qpu(
		_config(device), "cfg.yaml", provider="iqm")
	assert selected["resource_type"] == "IQMServer"


def test_select_qpu_omits_absent_resource_type(monkeypatch):
	# Absent has to stay absent, so descriptor.py's own fallback applies.
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(IQM_DEVICE), "cfg.yaml", provider="iqm")
	assert "resource-type" not in selected
	assert "resource_type" not in selected


# --- resolve_descriptor: a named non-IQM device has to resolve -------------

def test_resolve_descriptor_accepts_named_non_iqm_device(monkeypatch):
	# Regression guard. _selected_device used to pass provider=DEFAULT_PROVIDER
	# unconditionally, and select_qpu validates the chosen device against it,
	# so an IBM device was rejected with "expected 'iqm'" however it was
	# configured -- before any of the resource-type work could be reached.
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config", lambda path: _config(IBM_DEVICE))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	resolved = descriptor.resolve_descriptor()
	assert resolved["provider"] == "ibm"
	assert resolved["resource_type"] == "IBMQiskitRuntimeService"


def test_resolve_descriptor_resource_type_absent_is_none(monkeypatch):
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config", lambda path: _config(IQM_DEVICE))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	assert descriptor.resolve_descriptor()["resource_type"] is None


def test_resolve_descriptor_still_uses_provider_hint_when_unnamed(monkeypatch):
	# With no device named the provider hint still does its original job of
	# picking the IQM device out of the config.
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config", lambda path: _config(IQM_DEVICE))
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)

	assert descriptor.resolve_descriptor()["provider"] == "iqm"


# --- driver: the type comes from the descriptor ----------------------------

def test_driver_defaults_iqm_to_iqm_server():
	driver = QrmiDriver({"provider": "iqm"})
	assert driver._resource_type(_StubQrmi) == ("IQMServer", "IQMServer")


def test_driver_honors_explicit_resource_type():
	driver = QrmiDriver(
		{"provider": "ibm", "resource_type": "IBMQuantumSystem"})
	name, value = driver._resource_type(_StubQrmi)
	assert name == "IBMQuantumSystem"
	assert value == _StubResourceType.IBMQuantumSystem


def test_driver_explicit_type_wins_over_single_provider_default():
	driver = QrmiDriver(
		{"provider": "pasqal", "resource-type": "PasqalLocal"})
	assert driver._resource_type(_StubQrmi)[0] == "PasqalLocal"


def test_driver_rejects_ambiguous_provider():
	# IBM serves three types. Guessing would not fail here, it would fail much
	# later as an authentication error against the wrong endpoint, so this has
	# to be an error at resolution time and has to name the choices.
	driver = QrmiDriver({"provider": "ibm"})
	with pytest.raises(DEFwExecutionError) as excinfo:
		driver._resource_type(_StubQrmi)
	message = str(excinfo.value)
	assert "resource-type" in message
	for candidate in ("IBMQiskitRuntimeService", "IBMQuantumComputeService",
			"IBMQuantumSystem"):
		assert candidate in message


def test_driver_rejects_unknown_provider():
	driver = QrmiDriver({"provider": "acme"})
	with pytest.raises(DEFwExecutionError) as excinfo:
		driver._resource_type(_StubQrmi)
	assert "acme" in str(excinfo.value)


def test_driver_rejects_type_the_installed_qrmi_lacks():
	# Names resolve against the installed qrmi at call time, so a type this
	# build does not carry reports what it does carry rather than raising
	# AttributeError from inside the driver.
	driver = QrmiDriver({"provider": "ibm", "resource-type": "NotAThing"})
	with pytest.raises(DEFwExecutionError) as excinfo:
		driver._resource_type(_StubQrmi)
	message = str(excinfo.value)
	assert "NotAThing" in message
	assert "IQMServer" in message


def test_driver_skips_iqm_env_setup_for_non_iqm_types():
	# _ensure_iqm_isa_env writes IQM-shaped variables and raises when it cannot
	# resolve them, so it must not run for a non-IQM resource type.
	driver = QrmiDriver({"provider": "ibm"})
	called = []
	driver._ensure_iqm_isa_env = lambda *args, **kwargs: called.append(args)

	driver._ensure_resource_env("IBMQuantumSystem", "ibm_torino")
	assert called == []

	driver._ensure_resource_env("IQMServer", "default")
	assert len(called) == 1
