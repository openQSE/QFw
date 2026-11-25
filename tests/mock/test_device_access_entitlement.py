import json

import pytest

from defw_exception import DEFwExecutionError
from util import device_access


def _database(user_enabled=True, device_enabled=True, api_key="secret"):
	return {
		"users": {
			"alice": {
				"enabled": user_enabled,
				"devices": {
					"device-a": {
						"enabled": device_enabled,
						"api_key": api_key,
					},
				},
			},
		},
	}


def test_enabled_user_and_device_return_api_key():
	user, record = device_access.select_user_record(
		_database(), "alice", device_id="device-a")

	assert user == "alice"
	assert device_access.get_api_key_from_user_record(
		record, "device-a") == "secret"


@pytest.mark.parametrize("database", [
	_database(user_enabled=False),
	_database(device_enabled=False),
	_database(api_key=""),
	{"users": {"alice": {
		"devices": {"device-a": {"enabled": True, "api_key": "secret"}},
	}}},
	{"users": {"alice": {
		"enabled": True,
		"devices": {"device-a": {"api_key": "secret"}},
	}}},
])
def test_missing_or_disabled_entitlement_is_rejected(database):
	with pytest.raises(DEFwExecutionError, match="enabled entitlement"):
		device_access.select_user_record(
			database, "alice", device_id="device-a")


# --- which of the five rejections it was ------------------------------------
#
# The five cases above all raised the same sentence, which names an absent
# API key even when the real cause is a disabled entry or a device that is
# not listed. The message now says which step failed.

@pytest.mark.parametrize("database,expected", [
	(_database(user_enabled=False), "user entry is not enabled"),
	(_database(device_enabled=False), "'device-a' is not enabled"),
	(_database(api_key=""), "'device-a' has no usable api_key"),
	(_database(api_key="   "), "'device-a' has no usable api_key"),
	({"users": {"alice": {
		"enabled": True,
		"devices": {"device-b": {"enabled": True, "api_key": "secret"}},
	}}}, "no device named 'device-a'"),
	({"users": {"alice": {"enabled": True}}}, "no devices mapping"),
	({"users": {"bob": {"enabled": True}}}, "'alice' has no entry"),
	({"users": {"alice": "not-a-mapping"}}, "entry is not a mapping"),
])
def test_rejection_says_which_step_failed(database, expected):
	with pytest.raises(DEFwExecutionError) as raised:
		device_access.select_user_record(
			database, "alice", device_id="device-a")

	assert expected in str(raised.value)
	# The first line callers and docs already quote stays intact.
	assert "does not contain an enabled entitlement and API key" in str(
		raised.value)


def test_provider_device_id_is_named_when_neither_matches():
	database = {"users": {"alice": {"enabled": True, "devices": {}}}}

	with pytest.raises(DEFwExecutionError) as raised:
		device_access.select_user_record(
			database, "alice", device_id="device-a",
			provider_device_id="qpu:device-a")

	assert "'device-a' or 'qpu:device-a'" in str(raised.value)


def test_entitlement_only_rejection_does_not_mention_api_key():
	# select_entitled_record binds no API key, so an empty one is not a
	# reason to reject and must not show up in its diagnosis.
	with pytest.raises(DEFwExecutionError) as raised:
		device_access.select_entitled_record(
			_database(api_key="", device_enabled=False), "alice",
			device_id="device-a")

	assert "'device-a' is not enabled" in str(raised.value)
	assert "api_key" not in str(raised.value)


# --- a user's IBM service instance ------------------------------------------
#
# An IBM instance (CRN) is shared by its users rather than tied to one device,
# and a user can be assigned to several instances. So a user's entry can name
# the one to run under, and the device's service-crn is the default.

def _database_with_instance(crn="crn:alice", **kwargs):
	database = _database(**kwargs)
	database["users"]["alice"]["devices"]["device-a"]["service_crn"] = crn
	return database


def test_user_entry_names_its_ibm_instance():
	_user, record = device_access.select_user_record(
		_database_with_instance(), "alice", device_id="device-a")

	assert device_access.get_service_crn_from_user_record(
		record, "device-a") == "crn:alice"


def test_user_entry_without_an_instance_names_none():
	_user, record = device_access.select_user_record(
		_database(), "alice", device_id="device-a")

	assert device_access.get_service_crn_from_user_record(
		record, "device-a") is None


@pytest.mark.parametrize("user_enabled,device_enabled", [
	(False, True),
	(True, False),
])
def test_disabled_entitlement_names_no_instance(user_enabled, device_enabled):
	database = _database_with_instance(
		user_enabled=user_enabled, device_enabled=device_enabled)
	record = database["users"]["alice"]

	assert device_access.get_service_crn_from_user_record(
		record, "device-a") is None


def test_resolved_device_access_carries_the_users_instance(
		tmp_path, monkeypatch):
	(tmp_path / "qpu-users.json").write_text(
		json.dumps(_database_with_instance()), encoding="utf-8")
	config = tmp_path / "device-access.yaml"
	config.write_text(
		"qpus:\n"
		"  device-a:\n"
		"    provider: ibm\n"
		"    url: https://quantum.cloud.ibm.com/api/v1\n"
		"    credential-db: qpu-users.json\n",
		encoding="utf-8")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	access = device_access.resolve_device_access(
		device_id="device-a", user="alice")

	assert access["api_key"] == "secret"
	assert access["service_crn"] == "crn:alice"


# IBM Quantum System stages results through object storage. The store itself
# is described in device-access config, but the key pair is secret, so it
# comes from the user's credential DB entry the way the API key does.

def _database_with_object_storage(**kwargs):
	database = _database(**kwargs)
	database["users"]["alice"]["devices"]["device-a"].update({
		"aws_access_key_id": "AKIA-alice",
		"aws_secret_access_key": "secret-alice",
	})
	return database


def test_user_entry_carries_its_object_storage_key_pair():
	_user, record = device_access.select_user_record(
		_database_with_object_storage(), "alice", device_id="device-a")

	assert device_access.get_object_storage_from_user_record(
		record, "device-a") == {
			"aws_access_key_id": "AKIA-alice",
			"aws_secret_access_key": "secret-alice",
		}


def test_user_entry_without_object_storage_carries_nothing():
	_user, record = device_access.select_user_record(
		_database(), "alice", device_id="device-a")

	assert device_access.get_object_storage_from_user_record(
		record, "device-a") == {}


@pytest.mark.parametrize("user_enabled,device_enabled", [
	(False, True),
	(True, False),
])
def test_disabled_entitlement_carries_no_object_storage(
		user_enabled, device_enabled):
	database = _database_with_object_storage(
		user_enabled=user_enabled, device_enabled=device_enabled)
	record = database["users"]["alice"]

	assert device_access.get_object_storage_from_user_record(
		record, "device-a") == {}


def test_resolved_device_access_carries_the_store_and_the_key_pair(
		tmp_path, monkeypatch):
	(tmp_path / "qpu-users.json").write_text(
		json.dumps(_database_with_object_storage()), encoding="utf-8")
	config = tmp_path / "device-access.yaml"
	config.write_text(
		"qpus:\n"
		"  device-a:\n"
		"    provider: ibm\n"
		"    url: https://qs.example/api/v1\n"
		"    resource-type: IBMQuantumSystem\n"
		"    s3-endpoint: https://store.example\n"
		"    s3-bucket: results\n"
		"    s3-region: us-east\n"
		"    credential-db: qpu-users.json\n",
		encoding="utf-8")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	access = device_access.resolve_device_access(
		device_id="device-a", user="alice")

	assert access["aws_access_key_id"] == "AKIA-alice"
	assert access["aws_secret_access_key"] == "secret-alice"


def test_the_store_description_reaches_the_shim_descriptor(
		tmp_path, monkeypatch):
	# select_qpu forwards the shim's descriptor keys off the device entry.
	# Without this the QPM cannot learn where the bucket is.
	config = tmp_path / "device-access.yaml"
	config.write_text(
		"qpus:\n"
		"  device-a:\n"
		"    provider: ibm\n"
		"    url: https://qs.example/api/v1\n"
		"    resource-type: IBMQuantumSystem\n"
		"    s3-endpoint: https://store.example\n"
		"    s3-endpoint-for-qsapi: https://store.internal\n"
		"    s3-bucket: results\n"
		"    s3-region: us-east\n"
		"    credential-db: qpu-users.json\n",
		encoding="utf-8")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	device_config = device_access.load_yaml_config(str(config))
	selected = device_access.select_qpu(
		device_config, str(config), device_id="device-a")

	assert selected["s3-endpoint"] == "https://store.example"
	assert selected["s3-endpoint-for-qsapi"] == "https://store.internal"
	assert selected["s3-bucket"] == "results"
	assert selected["s3-region"] == "us-east"
