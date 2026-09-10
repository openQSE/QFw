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
