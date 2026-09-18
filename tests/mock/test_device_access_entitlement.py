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
