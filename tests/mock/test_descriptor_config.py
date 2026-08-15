# Guards the shim descriptor-config wiring: select_qpu() must forward the
# optional per-resource descriptor fields (libraries, preference, caps,
# execution-owner) so svc_lib_qpm's resolve_descriptor() can honor them. These
# keys used to be dropped by select_qpu's fixed whitelist, which silently
# defeated the descriptor customization documented in
# docs/qpu-frontend-contract.md section 5.
#
# Everything here works on plain dicts, so it needs neither PyYAML nor a live
# device: select_qpu() takes the parsed config dict directly, and the
# resolve_descriptor() end-to-end check stubs the config loader.

import importlib.util
import pathlib
import sys

import yaml


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

import util.device_access as device_access  # noqa: E402


DEVICE_WITH_DESCRIPTOR = {
	"provider": "iqm",
	"provider-device-id": "cocos",
	"url": "https://example.org/",
	"credential-db": "creds.json",
	"libraries": ["qrmi"],
	"preference": "qrmi",
	"execution-owner": "qrmi",
	"caps": {"get_device_info": ["qrmi"], "run_circuit": ["qrmi"]},
}

BARE_DEVICE = {
	"provider": "iqm",
	"url": "https://example.org/",
	"credential-db": "creds.json",
}

DESCRIPTOR_KEYS = (
	"libraries", "preference", "caps", "execution-owner", "execution_owner")


def _config(device, device_id="dev"):
	return {"qpus": {device_id: device}}


def _load_descriptor():
	# Load descriptor.py directly, bypassing svc_lib_qpm/__init__.py (which pulls
	# in the whole DEFw/api_events stack that is not available under tests/mock).
	spec = importlib.util.spec_from_file_location(
		"svc_lib_qpm_descriptor",
		str(REPO_ROOT / "services" / "svc_lib_qpm" / "descriptor.py"))
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_shim_example_uses_no_secret_credential_provider():
	config_path = REPO_ROOT / "examples" / "qfw_shim_device_access.yaml"
	config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
	device = config["qpus"]["ornl-iqm-20q"]

	assert device["credential-provider"] == "shim-no-secret"
	assert "credential-db" not in device
	assert config["credential-providers"]["shim-no-secret"] == {
		"type": "no-secret",
	}


# --- select_qpu(): the fix site --------------------------------------------

def test_select_qpu_passes_through_descriptor_fields(monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(DEVICE_WITH_DESCRIPTOR), "cfg.yaml", provider="iqm")
	assert selected["libraries"] == ["qrmi"]
	assert selected["preference"] == "qrmi"
	assert selected["execution-owner"] == "qrmi"
	assert selected["caps"] == {
		"get_device_info": ["qrmi"], "run_circuit": ["qrmi"]}


def test_select_qpu_omits_absent_descriptor_fields(monkeypatch):
	# Absent keys must NOT appear, so descriptor.py's device.get(key, DEFAULT)
	# fallbacks apply. Forwarding a key with a None value would defeat them.
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(BARE_DEVICE), "cfg.yaml", provider="iqm")
	for key in DESCRIPTOR_KEYS:
		assert key not in selected


def test_select_qpu_preserves_native_fields(monkeypatch):
	# Regression guard: the descriptor passthrough must not disturb the native
	# fields the IQM access path depends on.
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(DEVICE_WITH_DESCRIPTOR), "cfg.yaml", provider="iqm")
	assert selected["device_id"] == "dev"
	assert selected["provider"] == "iqm"
	assert selected["provider_device_id"] == "cocos"
	assert selected["url"] == "https://example.org/"
	assert selected["credential_db"].endswith("creds.json")


def test_select_qpu_accepts_named_provider_without_credential_file(
		monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	device = {
		"provider": "iqm",
		"provider-device-id": "default",
		"url": "https://example.org/",
		"credential-provider": "shim-no-secret",
	}
	selected = device_access.select_qpu(
		_config(device), "cfg.yaml", provider="iqm")

	assert selected["credential-provider"] == "shim-no-secret"
	assert "credential_db" not in selected


def test_select_qpu_accepts_execution_owner_underscore(monkeypatch):
	# Both spellings are honored; libraries pass through raw (resolve_descriptor
	# splits a comma string via _as_list).
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	device = dict(BARE_DEVICE, execution_owner="qdmi", libraries="qrmi, qdmi")
	selected = device_access.select_qpu(
		_config(device), "cfg.yaml", provider="iqm")
	assert selected["execution_owner"] == "qdmi"
	assert selected["libraries"] == "qrmi, qdmi"


# --- resolve_descriptor(): end-to-end over select_qpu ----------------------

def test_resolve_descriptor_honors_configured_fields(monkeypatch):
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: _config(DEVICE_WITH_DESCRIPTOR))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	resolved = descriptor.resolve_descriptor()
	assert resolved["libraries"] == ["qrmi"]
	assert resolved["preference"] == "qrmi"
	assert resolved["execution_owner"] == "qrmi"
	assert resolved["caps"] == {
		"get_device_info": ["qrmi"], "run_circuit": ["qrmi"]}


def test_resolve_descriptor_defaults_when_unconfigured(monkeypatch):
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: _config(BARE_DEVICE))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	resolved = descriptor.resolve_descriptor()
	assert resolved["libraries"] == descriptor.DEFAULT_LIBRARIES
	assert resolved["preference"] == descriptor.DEFAULT_PREFERENCE
	assert resolved["execution_owner"] == descriptor.DEFAULT_EXECUTION_OWNER
	assert resolved["caps"] == descriptor.DEFAULT_CAPS
