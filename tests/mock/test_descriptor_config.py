# Guards the shim descriptor-config wiring: select_qpu() must forward the
# optional per-resource descriptor fields (libraries, preference, caps,
# execution-owner) so svc_lib_qpm's resolve_descriptor() can honor them. These
# keys used to be dropped by select_qpu's fixed whitelist, which silently
# defeated the descriptor customization documented in
# docs/design/qpu-frontend-contract.md section 5.
#
# Everything here works on plain dicts, so it needs neither PyYAML nor a live
# device: select_qpu() takes the parsed config dict directly, and the
# resolve_descriptor() end-to-end check stubs the config loader.

import importlib.util
import pathlib
import sys

import pytest
import yaml

from defw_exception import DEFwExecutionError


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

# An IBM device carries its service instance and IAM endpoint in device-access
# config (#59 blocker 3), since a site service has no other way to get them.
IBM_CRN = "crn:v1:bluemix:public:quantum-computing:us-east:a/acct:inst::"
IBM_DEVICE = {
	"provider": "ibm",
	"provider-device-id": "ibm_torino",
	"url": "https://quantum.cloud.ibm.com/api/v1",
	"credential-db": "creds.json",
	"resource-type": "IBMQiskitRuntimeService",
	"service-crn": IBM_CRN,
	"iam-endpoint": "https://iam.test.cloud.ibm.com",
}

# IBMQuantumSystem additionally stages results through object storage and
# requires a job timeout. The store and the timeout describe the device, so
# they live beside it in device-access config; only the AWS key pair is secret
# and stays in the credential DB. The driver reads all of these off the
# descriptor, so select_qpu forwarding them is only half the path --
# resolve_descriptor has to carry them too (openQSE/QFw#76 wired the first half
# only).
IBM_QS_DEVICE = dict(IBM_DEVICE, **{
	"resource-type": "IBMQuantumSystem",
	"s3-endpoint": "https://s3.us-east.cloud-object-storage.appdomain.cloud",
	"s3-endpoint-for-qsapi": "https://s3.internal",
	"s3-bucket": "results-bucket",
	"s3-region": "us-east",
	"job-timeout-seconds": "30",
})

# An Amazon Braket device reached through QDMI. The ARN is the device, the
# stable QDMI id names the device library's catalogue entry, and the Region,
# results URI and reservation ARN are session and job settings. The qubit
# count and shot cap are what the service advertises and enforces for a
# device whose library does not say before a session opens.
SV1_ARN = "arn:aws:braket:::device/quantum-simulator/amazon/sv1"
AWS_DEVICE = {
	"provider": "aws",
	"provider-device-id": SV1_ARN,
	"qdmi-device-id": "amazon.braket.sv1",
	"aws-region": "us-east-1",
	"url": "https://braket.us-east-1.amazonaws.com",
	"s3-results-uri": "s3://results/qfw/sv1",
	"reservation-arn": "arn:aws:braket:us-east-1:123:reservation/r1",
	"num-qubits": 34,
	"max-shots": 1000,
	"credential-db": "creds.json",
	"libraries": ["qdmi"],
	"execution-owner": "qdmi",
}

DESCRIPTOR_KEYS = (
	"libraries", "preference", "caps", "execution_owner",
	"service-crn", "iam-endpoint", "qdmi-device-id", "aws-region",
	"s3-results-uri", "reservation-arn", "num-qubits", "max-shots")


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
	manifest_path = REPO_ROOT / "examples" / "qfw_shim_smoke_services.yaml"
	manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
	config_path = REPO_ROOT / "examples" / "qfw_shim_device_access.yaml"
	config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
	device = config["qpus"]["ornl-iqm-20q"]

	assert manifest["services"][0]["credential-mode"] == "no-secret"
	assert device["credential-provider"] == "shim-no-secret"
	assert "credential-db" not in device
	assert config["credential-providers"]["shim-no-secret"] == {
		"type": "no-secret",
	}
	# The Braket simulator entry the smoke can bind with --device-id aws-sv1.
	# Its identity is the process's AWS one, so no-secret is right here too.
	sv1 = config["qpus"]["aws-sv1"]
	assert sv1["provider"] == "aws"
	assert sv1["provider-device-id"] == SV1_ARN
	assert sv1["qdmi-device-id"] == "amazon.braket.sv1"
	assert sv1["credential-provider"] == "shim-no-secret"
	assert sv1["libraries"] == ["qdmi"]
	assert sv1["execution-owner"] == "qdmi"


# --- select_qpu(): the fix site --------------------------------------------

def test_select_qpu_passes_through_descriptor_fields(monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(DEVICE_WITH_DESCRIPTOR), "cfg.yaml", provider="iqm")
	assert selected["libraries"] == ["qrmi"]
	assert selected["preference"] == "qrmi"
	assert selected["execution_owner"] == "qrmi"
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

	assert selected["credential_provider"] == "shim-no-secret"
	assert "credential_db" not in selected


def test_select_qpu_passes_through_the_ibm_instance_fields(monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(IBM_DEVICE), "cfg.yaml", provider="ibm")
	assert selected["service-crn"] == IBM_CRN
	assert selected["iam-endpoint"] == "https://iam.test.cloud.ibm.com"


@pytest.mark.parametrize("key", [
	"provider_device_id",
	"quantum-computer",
	"quantum_computer",
	"credential_db",
	"credential_provider",
	"execution_owner",
])
def test_select_qpu_rejects_removed_device_keys(monkeypatch, key):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	device = dict(BARE_DEVICE)
	device[key] = "removed-value"
	with pytest.raises(DEFwExecutionError, match="unsupported QPU device"):
		device_access.select_qpu(
			_config(device), "cfg.yaml", provider="iqm")


@pytest.mark.parametrize("key,replacement", [
	("devices", "qpus"),
	("credential_providers", "credential-providers"),
])
def test_select_qpu_rejects_removed_top_level_keys(
		monkeypatch, key, replacement):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	config = _config(BARE_DEVICE)
	config[key] = {}
	with pytest.raises(DEFwExecutionError, match=replacement):
		device_access.select_qpu(config, "cfg.yaml", provider="iqm")


@pytest.mark.parametrize("key", [
	"credential_db",
	"refresh_policy",
	"ttl_ns",
	"ttl_seconds",
	"plugin_module",
	"class_name",
])
def test_select_qpu_rejects_removed_credential_provider_keys(
		monkeypatch, key):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	config = _config(BARE_DEVICE)
	config["credential-providers"] = {
		"site-provider": {key: "removed-value"},
	}
	with pytest.raises(DEFwExecutionError, match="credential provider"):
		device_access.select_qpu(config, "cfg.yaml", provider="iqm")


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
	assert resolved["service_crn"] is None
	assert resolved["iam_endpoint"] is None


def test_resolve_descriptor_carries_the_ibm_instance_fields(monkeypatch):
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: _config(IBM_DEVICE))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	resolved = descriptor.resolve_descriptor()
	assert resolved["provider"] == "ibm"
	assert resolved["resource_type"] == "IBMQiskitRuntimeService"
	assert resolved["service_crn"] == IBM_CRN
	assert resolved["iam_endpoint"] == "https://iam.test.cloud.ibm.com"


def test_select_qpu_passes_through_the_object_storage_fields(monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(IBM_QS_DEVICE), "cfg.yaml", provider="ibm")
	assert selected["s3-endpoint"] == IBM_QS_DEVICE["s3-endpoint"]
	assert selected["s3-endpoint-for-qsapi"] == "https://s3.internal"
	assert selected["s3-bucket"] == "results-bucket"
	assert selected["s3-region"] == "us-east"
	assert selected["job-timeout-seconds"] == "30"


def test_resolve_descriptor_carries_the_object_storage_fields(monkeypatch):
	# The regression: select_qpu forwarded the store, resolve_descriptor's own
	# fixed key set dropped it, and _ensure_object_storage_env then reported
	# S3_ENDPOINT, S3_BUCKET and S3_REGION missing for a device that configured
	# all three. A site service has no other way to receive them, so config
	# reaching the driver is the whole path.
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: _config(IBM_QS_DEVICE))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	resolved = descriptor.resolve_descriptor()
	assert resolved["resource_type"] == "IBMQuantumSystem"
	assert resolved["s3_endpoint"] == IBM_QS_DEVICE["s3-endpoint"]
	assert resolved["s3_endpoint_for_qsapi"] == "https://s3.internal"
	assert resolved["s3_bucket"] == "results-bucket"
	assert resolved["s3_region"] == "us-east"
	assert resolved["job_timeout_seconds"] == "30"


def test_select_qpu_passes_through_the_braket_fields(monkeypatch):
	monkeypatch.delenv(device_access.QPU_DEVICE_ENV, raising=False)
	selected = device_access.select_qpu(
		_config(AWS_DEVICE), "cfg.yaml", provider="aws")
	assert selected["provider_device_id"] == SV1_ARN
	assert selected["qdmi-device-id"] == "amazon.braket.sv1"
	assert selected["aws-region"] == "us-east-1"
	assert selected["s3-results-uri"] == "s3://results/qfw/sv1"
	assert selected["reservation-arn"] == AWS_DEVICE["reservation-arn"]
	assert selected["num-qubits"] == 34
	assert selected["max-shots"] == 1000


def test_resolve_descriptor_carries_the_braket_fields(monkeypatch):
	# Both halves of the path, the same way the object storage fields had to
	# be carried: select_qpu forwards and resolve_descriptor keeps.
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config",
		lambda path: _config(AWS_DEVICE))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	resolved = descriptor.resolve_descriptor()
	assert resolved["provider"] == "aws"
	assert resolved["provider_device_id"] == SV1_ARN
	assert resolved["qdmi_device_id"] == "amazon.braket.sv1"
	assert resolved["aws_region"] == "us-east-1"
	assert resolved["s3_results_uri"] == "s3://results/qfw/sv1"
	assert resolved["reservation_arn"] == AWS_DEVICE["reservation-arn"]
	assert resolved["num_qubits"] == 34
	assert resolved["max_shots"] == 1000
	assert resolved["libraries"] == ["qdmi"]
	assert resolved["execution_owner"] == "qdmi"


def test_resolve_descriptor_leaves_the_braket_fields_unset_when_unconfigured(
		monkeypatch):
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config", lambda path: _config(BARE_DEVICE))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	resolved = descriptor.resolve_descriptor()
	for key in ("qdmi_device_id", "aws_region", "s3_results_uri",
			"reservation_arn", "num_qubits", "max_shots"):
		assert resolved[key] is None


def test_resolve_descriptor_leaves_object_storage_unset_when_unconfigured(
		monkeypatch):
	# A device that stages nothing must not gain empty strings: the driver
	# treats a falsy descriptor value as "not configured" and falls back to
	# QFW_IBM_* or, for the timeout, to its own default.
	descriptor = _load_descriptor()
	monkeypatch.setattr(
		device_access, "device_access_config_path", lambda: "cfg.yaml")
	monkeypatch.setattr(
		device_access, "load_yaml_config", lambda path: _config(BARE_DEVICE))
	monkeypatch.setenv(device_access.QPU_DEVICE_ENV, "dev")

	resolved = descriptor.resolve_descriptor()
	for key in ("s3_endpoint", "s3_endpoint_for_qsapi", "s3_bucket",
			"s3_region", "job_timeout_seconds"):
		assert resolved[key] is None
