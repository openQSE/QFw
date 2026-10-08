# Guards the entitlement credential provider.
#
# A Braket device authenticates through the AWS SDK's own credential chain,
# so QFw holds no key for it. QFw still decides who may use the device. The
# file provider refuses to bind without an api_key, and no-secret skips the
# check and is refused for hardware, so neither fits. The entitlement
# provider checks the credential DB's enabled flags for the user and the
# device, binds no secret, and is accepted where credentials are required.
#
# Plain files and dicts throughout; no device library is needed.

import json

import pytest

from defw_exception import DEFwExecutionError
from util import device_access
from util.qpm import credentials


SV1_ARN = "arn:aws:braket:::device/quantum-simulator/amazon/sv1"

CONFIG = f"""\
qpus:
  aws-sv1:
    provider: aws
    provider-device-id: {SV1_ARN}
    url: https://braket.us-east-1.amazonaws.com
    credential-provider: aws-site-identity

credential-providers:
  aws-site-identity:
    type: entitlement
    credential-db: qpu-users.json
"""

# The same device, with a provider that names no database to read.
CONFIG_WITHOUT_DB = CONFIG.replace("    credential-db: qpu-users.json\n", "")


def _database(user_enabled=True, device_enabled=True):
	# No api_key anywhere: the entitlement is the whole record.
	return {
		"users": {
			"alice": {
				"enabled": user_enabled,
				"devices": {
					"aws-sv1": {"enabled": device_enabled},
				},
			},
		},
	}


def _site(tmp_path, monkeypatch, database, config_text=CONFIG):
	config = tmp_path / "device-access.yaml"
	config.write_text(config_text, encoding="utf-8")
	(tmp_path / "qpu-users.json").write_text(
		json.dumps(database), encoding="utf-8")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))
	return config


def _request(user="alice"):
	# The request the QPM controller builds from a reservation binding.
	return credentials.credential_request_from_binding({
		"resource": {"target_device_id": "aws-sv1"},
		"owner": {"user": user},
		"provider_credential_binding": {"provider": "aws"},
	})


def _provider(tmp_path, monkeypatch, database, config_text=CONFIG):
	_site(tmp_path, monkeypatch, database, config_text)
	return credentials.provider_for_request(
		_request(), credential_mode="required")


# --- binding --------------------------------------------------------------

def test_an_entitled_user_binds_without_a_secret(tmp_path, monkeypatch):
	provider = _provider(tmp_path, monkeypatch, _database())
	assert isinstance(provider, credentials.EntitlementCredentialProvider)

	response = provider.bind(_request())

	assert "api_key" not in response.secret
	assert "token" not in response.secret
	assert response.secret == {
		"url": "https://braket.us-east-1.amazonaws.com",
		"device_id": "aws-sv1",
		"provider": "aws",
		"provider_device_id": SV1_ARN,
		"quantum_computer": SV1_ARN,
		"user": "alice",
	}
	metadata = response.metadata
	assert metadata["provider"] == "aws-site-identity"
	assert metadata["provider_type"] == "entitlement"
	assert metadata["secret_material"] == "none"
	assert metadata["expires_at_ns"] == 0
	assert metadata["target_device_id"] == "aws-sv1"
	assert metadata["user"] == "alice"
	assert metadata["source"]["type"] == "entitlement"
	assert metadata["source"]["credential_db"].endswith("qpu-users.json")


@pytest.mark.parametrize("database", [
	_database(user_enabled=False),
	_database(device_enabled=False),
	{"users": {"alice": {"devices": {"aws-sv1": {"enabled": True}}}}},
	{"users": {"alice": {"enabled": True, "devices": {"aws-sv1": {}}}}},
	{"users": {"alice": {"enabled": True, "devices": {}}}},
	{"users": {"bob": {"enabled": True,
		"devices": {"aws-sv1": {"enabled": True}}}}},
])
def test_a_user_without_an_enabled_entry_is_refused(
		database, tmp_path, monkeypatch):
	provider = _provider(tmp_path, monkeypatch, database)

	with pytest.raises(
			credentials.QPMCredentialBindingMissing,
			match="enabled entitlement for user 'alice'"):
		provider.bind(_request())
	with pytest.raises(credentials.QPMCredentialBindingMissing):
		provider.validate(_request())


def test_validate_passes_for_an_entitled_user(tmp_path, monkeypatch):
	provider = _provider(tmp_path, monkeypatch, _database())

	assert provider.validate(_request()) is None


def test_the_reservation_path_binds_through_it(tmp_path, monkeypatch):
	_site(tmp_path, monkeypatch, _database())

	provider, response = credentials.bind_reservation_credential({
		"resource": {"target_device_id": "aws-sv1"},
		"owner": {"user": "alice"},
		"provider_credential_binding": {"provider": "aws"},
	}, credential_mode="required")

	assert isinstance(provider, credentials.EntitlementCredentialProvider)
	assert response.secret["device_id"] == "aws-sv1"


def test_an_entitlement_provider_needs_a_database(tmp_path, monkeypatch):
	provider = _provider(
		tmp_path, monkeypatch, _database(), config_text=CONFIG_WITHOUT_DB)

	with pytest.raises(
			credentials.QPMCredentialProviderUnavailable,
			match="requires credential-db"):
		provider.bind(_request())


# --- the device-access lookup -------------------------------------------------

def test_resolve_device_access_checks_the_entitlement_and_returns_no_key(
		tmp_path, monkeypatch):
	_site(tmp_path, monkeypatch, _database())
	monkeypatch.setenv("QFW_USER", "alice")

	access = device_access.resolve_device_access(device_id="aws-sv1")

	assert access["api_key"] is None
	assert access["user"] == "alice"
	assert access["provider"] == "aws"
	assert access["provider_device_id"] == SV1_ARN
	assert access["url"] == "https://braket.us-east-1.amazonaws.com"


def test_resolve_device_access_refuses_a_user_who_is_not_entitled(
		tmp_path, monkeypatch):
	_site(tmp_path, monkeypatch, _database(device_enabled=False))
	monkeypatch.setenv("QFW_USER", "alice")

	with pytest.raises(DEFwExecutionError, match="enabled entitlement"):
		device_access.resolve_device_access(device_id="aws-sv1")


def test_resolve_device_access_names_a_missing_database(
		tmp_path, monkeypatch):
	_site(tmp_path, monkeypatch, _database(), config_text=CONFIG_WITHOUT_DB)
	monkeypatch.setenv("QFW_USER", "alice")

	with pytest.raises(
			DEFwExecutionError,
			match="needs a credential-db to read entitlements from"):
		device_access.resolve_device_access(device_id="aws-sv1")


def test_select_entitled_record_needs_no_api_key():
	user, record = device_access.select_entitled_record(
		_database(), "alice", device_id="aws-sv1")

	assert user == "alice"
	assert record["enabled"] is True
	assert device_access.get_api_key_from_user_record(
		record, "aws-sv1") is None


def test_select_entitled_record_matches_the_provider_device_id():
	database = {"users": {"alice": {"enabled": True,
		"devices": {SV1_ARN: {"enabled": True}}}}}

	user, _record = device_access.select_entitled_record(
		database, "alice", device_id="aws-sv1", provider_device_id=SV1_ARN)

	assert user == "alice"


# --- the service launcher's configuration check ----------------------------

def test_required_credentials_accept_the_entitlement_type(
		tmp_path, monkeypatch):
	config = _site(tmp_path, monkeypatch, _database())

	device = device_access.validate_credential_configuration(
		str(config), "aws-sv1")

	assert device["credential_provider"] == "aws-site-identity"
	assert device["credential_provider_type"] == "entitlement"
	assert device["credential_db"].endswith("qpu-users.json")
