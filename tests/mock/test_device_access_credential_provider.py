# A device may name a credential provider instead of a credential database,
# and select_qpu accepts either. resolve_qpu_credentials then read
# device["credential_db"] unconditionally, so a device that named a provider
# raised KeyError, surfaced to the caller as a complaint about the
# configuration it had actually got right (openQSE/QFw#78).

import json

import pytest

from defw_exception import DEFwExecutionError
from util import device_access


def _config(tmp_path, body):
	config = tmp_path / "device-access.yaml"
	config.write_text(body, encoding="utf-8")
	return config


def _select(config, device_id="ornl-iqm-20q"):
	device_config = device_access.load_yaml_config(str(config))
	return device_access.select_qpu(
		device_config, str(config), device_id=device_id)


# The config QFw ships, from examples/qfw_shim_device_access.yaml.
SHIPPED = """\
qpus:
  ornl-iqm-20q:
    provider: iqm
    provider-device-id: default
    url: https://qccsw.ccs.ornl.gov/
    credential-provider: shim-no-secret

credential-providers:
  shim-no-secret:
    type: no-secret
"""


def test_the_shipped_no_secret_config_resolves(tmp_path, monkeypatch):
	# The regression. This is the config qfw_shim_smoke.sh builds its site
	# config from, so the failure needed nothing unusual.
	config = _config(tmp_path, SHIPPED)
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))
	monkeypatch.setenv("QFW_USER", "alice")

	access = device_access.resolve_device_access(provider="iqm")

	# Returned as configured. The drivers strip the trailing slash, not this
	# layer.
	assert access["url"] == "https://qccsw.ccs.ornl.gov/"
	assert access["provider_device_id"] == "default"
	# A no-secret provider has no key to find. The caller reports a missing
	# token itself, naming what to set, which is the useful message.
	assert access["api_key"] is None
	assert access["user"] == "alice"


def test_a_no_secret_device_is_typed_by_select_qpu(tmp_path):
	selected = _select(_config(tmp_path, SHIPPED))

	assert selected["credential_provider"] == "shim-no-secret"
	assert selected["credential_provider_type"] == "no-secret"
	assert "credential_db" not in selected


FILE_PROVIDER = """\
qpus:
  ornl-iqm-20q:
    provider: iqm
    provider-device-id: default
    url: https://qccsw.ccs.ornl.gov/
    credential-provider: site-file

credential-providers:
  site-file:
    type: file
    {key}: qpu-users.json
"""


@pytest.mark.parametrize("key", ["credential-db", "path"])
def test_a_file_provider_supplies_the_database(key, tmp_path, monkeypatch):
	# FileCredentialProvider reads credential-db or path off the provider,
	# so this path honours the same two spellings.
	(tmp_path / "qpu-users.json").write_text(json.dumps({
		"users": {
			"alice": {
				"enabled": True,
				"devices": {
					"ornl-iqm-20q": {"enabled": True, "api_key": "secret"},
				},
			},
		},
	}), encoding="utf-8")
	config = _config(tmp_path, FILE_PROVIDER.format(key=key))
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	access = device_access.resolve_device_access(
		provider="iqm", user="alice")

	assert access["api_key"] == "secret"


def test_an_inline_provider_mapping_works(tmp_path, monkeypatch):
	(tmp_path / "qpu-users.json").write_text(json.dumps({
		"users": {
			"alice": {
				"enabled": True,
				"devices": {
					"ornl-iqm-20q": {"enabled": True, "api_key": "inline"},
				},
			},
		},
	}), encoding="utf-8")
	config = _config(tmp_path, """\
qpus:
  ornl-iqm-20q:
    provider: iqm
    url: https://qccsw.ccs.ornl.gov/
    credential-provider:
      type: file
      credential-db: qpu-users.json
""")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	access = device_access.resolve_device_access(
		provider="iqm", user="alice")

	assert access["api_key"] == "inline"


def test_a_device_database_still_wins(tmp_path, monkeypatch):
	(tmp_path / "on-device.json").write_text(json.dumps({
		"users": {
			"alice": {
				"enabled": True,
				"devices": {
					"ornl-iqm-20q": {"enabled": True, "api_key": "device"},
				},
			},
		},
	}), encoding="utf-8")
	config = _config(tmp_path, """\
qpus:
  ornl-iqm-20q:
    provider: iqm
    url: https://qccsw.ccs.ornl.gov/
    credential-db: on-device.json
    credential-provider: site-file

credential-providers:
  site-file:
    type: file
    credential-db: from-provider.json
""")

	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	access = device_access.resolve_device_access(
		provider="iqm", user="alice")

	assert access["api_key"] == "device"


def test_a_plugin_provider_says_it_needs_a_reservation(tmp_path, monkeypatch):
	config = _config(tmp_path, """\
qpus:
  ornl-iqm-20q:
    provider: iqm
    url: https://qccsw.ccs.ornl.gov/
    credential-provider: vault

credential-providers:
  vault:
    type: python
    module: site_vault
""")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	with pytest.raises(DEFwExecutionError) as excinfo:
		device_access.resolve_device_access(provider="iqm", user="alice")

	message = str(excinfo.value)
	assert "ornl-iqm-20q" in message
	assert "vault" in message
	assert "QFW_API_KEY" in message


def test_an_undefined_provider_name_is_named(tmp_path, monkeypatch):
	# select_qpu stays permissive about this, deliberately: the reservation
	# path in util.qpm.credentials treats an unresolvable name as a file
	# provider too. The complaint belongs where a credential is actually
	# needed.
	config = _config(tmp_path, """\
qpus:
  ornl-iqm-20q:
    provider: iqm
    url: https://qccsw.ccs.ornl.gov/
    credential-provider: missing-provider

credential-providers:
  site-file:
    type: file
    credential-db: qpu-users.json
""")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	selected = _select(config)
	assert selected["credential_provider_defined"] is False

	with pytest.raises(DEFwExecutionError) as excinfo:
		device_access.resolve_device_access(provider="iqm", user="alice")

	message = str(excinfo.value)
	assert "missing-provider" in message
	assert "not defined under credential-providers" in message


def test_a_file_provider_without_a_database_is_named(tmp_path, monkeypatch):
	config = _config(tmp_path, """\
qpus:
  ornl-iqm-20q:
    provider: iqm
    url: https://qccsw.ccs.ornl.gov/
    credential-provider: site-file

credential-providers:
  site-file:
    type: file
""")
	monkeypatch.setenv(device_access.DEVICE_ACCESS_CONFIG_ENV, str(config))

	with pytest.raises(DEFwExecutionError) as excinfo:
		device_access.resolve_device_access(provider="iqm", user="alice")

	message = str(excinfo.value)
	assert "site-file" in message
	assert "credential-db" in message
