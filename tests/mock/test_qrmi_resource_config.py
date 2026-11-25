# QRMI 0.25.0 added QuantumResource.from_config(), which takes a resource's
# settings as a dict instead of reading them from the process environment. The
# driver prefers it, because the environment is what made two reservations in
# one shim process able to tread on each other: the settings are process-wide,
# and QRMI through 0.24.4 read the IBM Quantum System object-storage pair
# inside task_result() rather than at construction, so a second reservation
# opening its resource could replace the AWS key a first reservation had yet
# to read.
#
# Two things these guard. The map is built by ONE resolver shared with the
# environment path, so the two cannot drift; test_the_two_paths_agree is the
# check that matters, since the precedence is subtle (a credential replaces, a
# bare call fills in, the CRN has a four-level order). And the map STARTS from
# the environment, because QRMI ignores the environment once a map is given
# and reads settings this driver does not model, including the acquisition
# token and the QRS/QCS session settings that the SPANK plugin sets.

import os
import pathlib
import sys
import threading
import types

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402


BACKEND = "ibm_torino"
PASQAL_BACKEND = "EMU_FREE"
PASQAL_ACCESS = {
	"base_url": "https://apis.pasqal.cloud",
	"token": "pasqal-tok",
	"project_id": "proj-alice",
}
SUFFIXES = (
	"ENDPOINT", "IAM_ENDPOINT", "IAM_APIKEY", "SERVICE_CRN",
	"S3_ENDPOINT", "S3_ENDPOINT_FOR_QSAPI", "S3_BUCKET", "S3_REGION",
	"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
)
QFW_VARS = (
	"QFW_IBM_SERVICE_CRN", "QFW_IBM_IAM_ENDPOINT",
	"QFW_IBM_S3_ENDPOINT", "QFW_IBM_S3_ENDPOINT_FOR_QSAPI",
	"QFW_IBM_S3_BUCKET", "QFW_IBM_S3_REGION",
	"QFW_IBM_AWS_ACCESS_KEY_ID", "QFW_IBM_AWS_SECRET_ACCESS_KEY",
	"QFW_IBM_JOB_TIMEOUT_SECONDS", "QFW_QC_URL", "QFW_API_KEY",
)

STORE = {
	"s3-endpoint": "https://store.example",
	"s3-endpoint-for-qsapi": "https://store.internal",
	"s3-bucket": "results",
	"s3-region": "us-east",
}
KEY_PAIR = {
	"aws_access_key_id": "AKIA-db",
	"aws_secret_access_key": "secret-db",
}


def _clear():
	for name in list(os.environ):
		if name.startswith((
				f"{BACKEND}_", f"{PASQAL_BACKEND}_", "default_")):
			del os.environ[name]
	for name in QFW_VARS:
		os.environ.pop(name, None)


@pytest.fixture(autouse=True)
def clean_env():
	# These helpers write into os.environ by design, so every test starts and
	# ends from a known-empty set rather than inheriting another test's writes.
	_clear()
	yield
	_clear()


def _driver(access=None, **descriptor):
	descriptor.setdefault("provider", "ibm")
	driver = QrmiDriver(descriptor)
	resolved = {"base_url": "https://example.org", "token": "tok"}
	if access is not None:
		resolved = access
	driver._access = lambda credential=None: dict(resolved)
	return driver


def _qs_driver(**descriptor):
	descriptor.update(STORE)
	access = {"base_url": "https://example.org", "token": "tok"}
	access.update(KEY_PAIR)
	return _driver(access=access, **descriptor)


def _env_settings(make_driver, type_name, alias, credential=None):
	# What the environment path leaves behind, as unprefixed keys, so it can
	# be compared with a config map.
	_clear()
	driver = make_driver()
	driver._ensure_resource_env(type_name, alias, credential=credential)
	backend = alias.split(",")[0]
	prefix = f"{backend}_"
	return {
		name[len(prefix):]: value
		for name, value in os.environ.items()
		if name.startswith(f"{prefix}QRMI_")
	}


def _config_settings(make_driver, type_name, alias, credential=None):
	_clear()
	return make_driver()._resource_config(
		type_name, alias, credential=credential)


# What to resolve, as (label, driver factory, resource type, alias,
# credential). Each is run down both paths and the results compared.
CASES = (
	(
		"qrs without a credential",
		lambda: _driver(service_crn="crn:device"),
		"IBMQiskitRuntimeService", BACKEND, None,
	),
	(
		"qrs with a credential",
		lambda: _driver(service_crn="crn:device"),
		"IBMQiskitRuntimeService", BACKEND,
		{"user": "alice", "service_crn": "crn:alice"},
	),
	(
		"qrs with a credential that names no instance",
		lambda: _driver(service_crn="crn:device"),
		"IBMQiskitRuntimeService", BACKEND, {"user": "alice"},
	),
	(
		"qcs picks its own family",
		lambda: _driver(service_crn="crn:device"),
		"IBMQuantumComputeService", BACKEND, None,
	),
	(
		"quantum system without a credential",
		lambda: _qs_driver(service_crn="crn:device"),
		"IBMQuantumSystem", BACKEND, None,
	),
	(
		"quantum system with a credential",
		lambda: _qs_driver(service_crn="crn:device"),
		"IBMQuantumSystem", BACKEND,
		dict({"user": "alice", "service_crn": "crn:alice"}, **KEY_PAIR),
	),
	(
		"quantum system with a device job timeout",
		lambda: _qs_driver(service_crn="crn:device", **{
			"job-timeout-seconds": 900}),
		"IBMQuantumSystem", BACKEND, None,
	),
	(
		"iqm without a credential",
		lambda: _driver(provider="iqm"),
		"IQMServer", "default", None,
	),
	(
		"iqm with a credential",
		lambda: _driver(provider="iqm"),
		"IQMServer", "default", {"user": "alice"},
	),
	(
		"iqm alias carrying a calibration set",
		lambda: _driver(provider="iqm"),
		"IQMServer", "default,cal-7", None,
	),
)


@pytest.mark.parametrize(
	"label,make_driver,type_name,alias,credential",
	CASES, ids=[case[0] for case in CASES])
def test_the_two_paths_agree(
		label, make_driver, type_name, alias, credential):
	# The anti-drift check. One resolver feeds both sinks, so a config map and
	# the variables the environment path writes have to come out identical.
	# Starting from an empty environment there is nothing to carry over, so
	# the map is exactly what was resolved.
	from_env = _env_settings(
		make_driver, type_name, alias, credential=credential)
	from_config = _config_settings(
		make_driver, type_name, alias, credential=credential)

	assert from_config == from_env, label


@pytest.mark.parametrize("provider,type_name", (
	("pasqal", "PasqalLocal"),
	("alicebob", "AliceBobFelis"),
))
def test_a_type_with_nothing_to_resolve_has_no_config(provider, type_name):
	# PasqalLocal and Alice & Bob have no device-access mapping, so the
	# environment is still their only source and _qpu must fall back to it.
	assert _driver(provider=provider)._resource_config(
		type_name, "backend") is None


def test_pasqal_cloud_resolves_token_project_and_base_url():
	config = _driver(
		access=dict(PASQAL_ACCESS, project_id=None), provider="pasqal",
		project_id="proj-device",
	)._resource_config("PasqalCloud", PASQAL_BACKEND)

	assert config == {
		"QRMI_PASQAL_CLOUD_AUTH_TOKEN": "pasqal-tok",
		"QRMI_PASQAL_CLOUD_PROJECT_ID": "proj-device",
		"QRMI_PASQAL_CLOUD_BASE_URL": "https://apis.pasqal.cloud",
	}


# --- whose identity a Pasqal resource runs as ---------------------------------

OPERATOR_LOGIN = {
	"QRMI_PASQAL_CLOUD_CLIENT_ID": "operator-id",
	"QRMI_PASQAL_CLOUD_CLIENT_SECRET": "operator-secret",
}


def _operator_env(**extra):
	# A service set up to log in as itself: a service account, a token and
	# a project for the backend, and Pasqal's own config root and login.
	for key, value in dict(OPERATOR_LOGIN, **{
			"QRMI_PASQAL_CLOUD_AUTH_TOKEN": "operator-tok",
			"QRMI_PASQAL_CLOUD_PROJECT_ID": "proj-operator"}).items():
		os.environ[f"{PASQAL_BACKEND}_{key}"] = value
	os.environ.update(extra)


@pytest.fixture
def pasqal_login_env(monkeypatch):
	for name in ("PASQAL_CONFIG_ROOT", "PASQAL_USERNAME", "PASQAL_PASSWORD"):
		monkeypatch.delenv(name, raising=False)
	return monkeypatch


def test_a_users_token_never_travels_with_the_services_login(pasqal_login_env):
	# QRMI renews an expiring token with whatever login it holds, so a user's
	# resource carrying the operator's service account would carry on as the
	# operator once the user's token expires.
	_operator_env(
		PASQAL_CONFIG_ROOT="/etc/qfw/pasqal",
		PASQAL_USERNAME="operator", PASQAL_PASSWORD="operator-pw")

	config = _driver(access=PASQAL_ACCESS, provider="pasqal")._resource_config(
		"PasqalCloud", PASQAL_BACKEND, credential={
			"user": "alice", "token": "alice-tok",
			"project_id": "proj-alice", "reservation_id": 7,
			"url": "https://apis.pasqal.cloud"})

	assert config == {
		"QRMI_PASQAL_CLOUD_AUTH_TOKEN": "alice-tok",
		"QRMI_PASQAL_CLOUD_PROJECT_ID": "proj-alice",
		"QRMI_PASQAL_CLOUD_BASE_URL": "https://apis.pasqal.cloud",
	}


def test_a_reservation_id_alone_is_not_a_user_credential(pasqal_login_env):
	# Every circuit's credential carries its reservation id, the no-secret
	# provider's nothing else. That is the service's own identity, resolved
	# exactly as without a reservation.
	_operator_env()
	driver = _driver(access={}, provider="pasqal")

	bare = driver._resource_config("PasqalCloud", PASQAL_BACKEND)
	reserved = driver._resource_config(
		"PasqalCloud", PASQAL_BACKEND, credential={"reservation_id": 7})

	assert reserved == bare == dict(OPERATOR_LOGIN, **{
		"QRMI_PASQAL_CLOUD_AUTH_TOKEN": "operator-tok",
		"QRMI_PASQAL_CLOUD_PROJECT_ID": "proj-operator"})


def test_an_entitled_user_runs_as_the_service_billed_to_their_project(
		pasqal_login_env):
	_operator_env()
	config = _driver(access={}, provider="pasqal")._resource_config(
		"PasqalCloud", PASQAL_BACKEND, credential={
			"user": "alice", "project_id": "proj-alice",
			"reservation_id": 7})

	assert config["QRMI_PASQAL_CLOUD_CLIENT_ID"] == "operator-id"
	assert config["QRMI_PASQAL_CLOUD_PROJECT_ID"] == "proj-alice"


def test_the_service_can_log_in_as_pasqals_own_tools_do(pasqal_login_env):
	# Opted into explicitly: a config root, or a username and password. The
	# service's $HOME is never handed over, so QRMI reads no config file the
	# operator did not point at.
	pasqal_login_env.setenv(
		f"{PASQAL_BACKEND}_PASQAL_CONFIG_ROOT", "/etc/qfw/pasqal")
	pasqal_login_env.setenv("PASQAL_USERNAME", "operator")
	pasqal_login_env.setenv("PASQAL_PASSWORD", "operator-pw")
	pasqal_login_env.setenv("HOME", "/home/qfw")

	config = _driver(access={}, provider="pasqal")._resource_config(
		"PasqalCloud", PASQAL_BACKEND)

	assert config == {
		"PASQAL_CONFIG_ROOT": "/etc/qfw/pasqal",
		"PASQAL_USERNAME": "operator",
		"PASQAL_PASSWORD": "operator-pw",
	}


def test_a_users_resource_resolves_nothing_for_the_service_user():
	# A token without a URL or a user (a plugin provider's, say) is still
	# the user's: the device supplies the rest, never the service's own
	# credential DB entry and its project.
	driver = _driver(provider="pasqal", project_id="proj-device",
		url="https://pasqal.example/")

	def refuse(credential=None):
		raise AssertionError("resolved device access for the service user")
	driver._access = refuse

	config = driver._resource_config(
		"PasqalCloud", PASQAL_BACKEND, credential={"token": "alice-tok"})

	assert config == {
		"QRMI_PASQAL_CLOUD_AUTH_TOKEN": "alice-tok",
		"QRMI_PASQAL_CLOUD_PROJECT_ID": "proj-device",
		"QRMI_PASQAL_CLOUD_BASE_URL": "https://pasqal.example",
	}


def test_a_failed_device_lookup_keeps_the_device_url():
	# The service's own user may hold no credential DB entry; the device's
	# URL must not fall back to Pasqal Cloud's public one for that.
	driver = _driver(provider="pasqal", url="https://pasqal.example")

	def unresolved(credential=None):
		raise DEFwExecutionError("no entry for the service user")
	driver._access = unresolved

	config = driver._resource_config("PasqalCloud", PASQAL_BACKEND)

	assert config["QRMI_PASQAL_CLOUD_BASE_URL"] == "https://pasqal.example"


def test_pasqal_cloud_opens_without_a_project():
	# An emulator's specs are public, so discovery needs no project; a batch
	# does, and run_circuit asks for it (test_qrmi_pasqal).
	config = _driver(
		access=dict(PASQAL_ACCESS, project_id=None), provider="pasqal",
	)._resource_config("PasqalCloud", PASQAL_BACKEND)

	assert "QRMI_PASQAL_CLOUD_PROJECT_ID" not in config


def test_a_qrmi_without_from_config_cannot_open_pasqal_cloud():
	# Its constructor reads $HOME/.pasqal/config and PASQAL_USERNAME beside
	# whatever token the environment holds.
	with pytest.raises(DEFwExecutionError, match="qrmi 0.25.0 or later"):
		_driver(provider="pasqal")._ensure_resource_env(
			"PasqalCloud", PASQAL_BACKEND,
			credential={"token": "alice-tok"})


@pytest.mark.parametrize("type_name,alias,settings", (
	("IQMServer", "default", {
		"QRMI_IQM_ISA_ENDPOINT": "https://iqm.example",
		"QRMI_IQM_ISA_TOKEN": "spank-tok",
	}),
	("IBMQiskitRuntimeService", BACKEND, {
		"QRMI_IBM_QRS_ENDPOINT": "https://qrs.example",
		"QRMI_IBM_QRS_IAM_APIKEY": "operator-key",
		"QRMI_IBM_QRS_SERVICE_CRN": "crn:operator",
	}),
))
@pytest.mark.parametrize("credential", (
	{"reservation_id": 7},
	{"reservation_id": 7, "user": "alice", "device_id": "dev"},
), ids=("no-secret", "entitlement"))
def test_a_reservation_credential_without_a_key_keeps_the_environment(
		type_name, alias, settings, credential):
	# Every circuit's credential carries its reservation id. The no-secret
	# provider's carries nothing else, and the entitlement provider's a user
	# but no key. Neither names an identity to open with, so the settings
	# SPANK or the operator put in the environment still apply, exactly as
	# without a credential, even with no device access to fall back to.
	backend = alias.split(",")[0]
	for key, value in settings.items():
		os.environ[f"{backend}_{key}"] = value
	driver = QrmiDriver({"provider": type_name[:3].lower()})

	def no_device_access(credential=None):
		raise DEFwExecutionError("no device access configured")

	driver._access = no_device_access

	bare = driver._resource_config(type_name, alias)
	reserved = driver._resource_config(
		type_name, alias, credential=credential)

	assert reserved == bare
	assert settings.items() <= reserved.items()


def test_the_map_carries_settings_the_driver_does_not_model(monkeypatch):
	# QRMI ignores the environment once a map is given, and it reads more than
	# this driver resolves. The SPANK plugin sets the acquisition token and
	# SESSION_MODE, so dropping them would change how a job runs.
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	monkeypatch.setenv(f"{BACKEND}_QRMI_JOB_ACQUISITION_TOKEN", "acq-token")
	monkeypatch.setenv(f"{BACKEND}_QRMI_IBM_QRS_SESSION_MODE", "dedicated")
	monkeypatch.setenv(f"{BACKEND}_QRMI_IBM_QRS_SESSION_MAX_TTL", "28800")

	config = _driver()._resource_config("IBMQiskitRuntimeService", BACKEND)

	assert config["QRMI_JOB_ACQUISITION_TOKEN"] == "acq-token"
	assert config["QRMI_IBM_QRS_SESSION_MODE"] == "dedicated"
	assert config["QRMI_IBM_QRS_SESSION_MAX_TTL"] == "28800"


def test_only_this_backends_settings_are_carried(monkeypatch):
	# The environment holds one family per resource id, so another device's
	# variables must not leak into this one's map.
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	monkeypatch.setenv("other_device_QRMI_IBM_QRS_SESSION_MODE", "batch")
	monkeypatch.setenv("QRMI_IBM_QRS_SESSION_MODE", "batch")
	try:
		config = _driver()._resource_config(
			"IBMQiskitRuntimeService", BACKEND)
		assert "QRMI_IBM_QRS_SESSION_MODE" not in config
	finally:
		monkeypatch.delenv(
			"other_device_QRMI_IBM_QRS_SESSION_MODE", raising=False)


def test_a_credential_clears_what_it_does_not_supply(monkeypatch):
	# The reason the resolver returns None for an absent value rather than
	# leaving it: a CRN left over in the environment would otherwise run this
	# reservation under the previous user's instance.
	monkeypatch.setenv(f"{BACKEND}_QRMI_IBM_QRS_SERVICE_CRN", "crn:previous")

	config = _driver()._resource_config(
		"IBMQiskitRuntimeService", BACKEND,
		credential={"user": "alice", "service_crn": "crn:alice"})
	assert config["QRMI_IBM_QRS_SERVICE_CRN"] == "crn:alice"

	with pytest.raises(DEFwExecutionError) as excinfo:
		_driver()._resource_config(
			"IBMQiskitRuntimeService", BACKEND,
			credential={"user": "bob"})
	assert f"{BACKEND}_QRMI_IBM_QRS_SERVICE_CRN" in str(excinfo.value)


def test_a_credential_clears_the_previous_key_pair(monkeypatch):
	# Same for the object-storage pair, which is the leak this change closes.
	monkeypatch.setenv(
		f"{BACKEND}_QRMI_IBM_QS_AWS_ACCESS_KEY_ID", "AKIA-previous")
	monkeypatch.setenv(
		f"{BACKEND}_QRMI_IBM_QS_AWS_SECRET_ACCESS_KEY", "secret-previous")

	config = _qs_driver(service_crn="crn:device")._resource_config(
		"IBMQuantumSystem", BACKEND, credential=dict(
			{"user": "alice"},
			aws_access_key_id="AKIA-alice",
			aws_secret_access_key="secret-alice"))

	assert config["QRMI_IBM_QS_AWS_ACCESS_KEY_ID"] == "AKIA-alice"
	assert config["QRMI_IBM_QS_AWS_SECRET_ACCESS_KEY"] == "secret-alice"


def test_missing_settings_are_reported_before_qrmi_is_opened():
	# The same message the environment path raises, naming the prefixed
	# variables, which stay actionable because the map starts from them.
	with pytest.raises(DEFwExecutionError) as excinfo:
		_driver()._resource_config("IBMQiskitRuntimeService", BACKEND)
	message = str(excinfo.value)
	assert f"{BACKEND}_QRMI_IBM_QRS_SERVICE_CRN" in message
	assert "service-crn" in message
	assert "SPANK" not in message


# --------------------------------------------------------------------------
# _qpu's choice of path, and what the config path does not touch.

class _EnvOnlyResource:
	# Stands in for a qrmi that predates from_config(), which the driver must
	# still open through the environment. It records how it was opened.
	def __init__(self, alias, resource_type, config=None):
		self.alias = alias
		self.config = config
		# What the environment held while this was constructed, so a test can
		# tell whether the settings went through it.
		self.env = {
			name: value for name, value in os.environ.items()
			if name.startswith(f"{alias.split(',')[0]}_QRMI_")
		}


class _Resource(_EnvOnlyResource):
	# And one that carries from_config(), which the driver should prefer.
	@staticmethod
	def from_config(alias, resource_type, config):
		return _Resource(alias, resource_type, config=dict(config))


def _wired(resource_class, **descriptor):
	descriptor.setdefault("provider", "ibm")
	descriptor.setdefault("resource-type", "IBMQiskitRuntimeService")
	descriptor.setdefault("provider-device-id", BACKEND)
	descriptor.setdefault("service_crn", "crn:device")
	driver = _driver(**descriptor)
	driver._qrmi = types.SimpleNamespace(
		ResourceType=types.SimpleNamespace(
			IBMQiskitRuntimeService="IBMQiskitRuntimeService"),
		QuantumResource=resource_class)
	return driver


def test_the_config_path_never_writes_the_environment():
	# The point of the change. Nothing about opening a resource should leave a
	# credential in a process-wide variable.
	driver = _wired(_Resource)

	resource = driver._qpu(credential={"user": "alice"})

	assert resource.config["QRMI_IBM_QRS_IAM_APIKEY"] == "tok"
	assert resource.env == {}
	leaked = [name for name in os.environ if name.startswith(f"{BACKEND}_")]
	assert leaked == []


def test_a_qrmi_without_from_config_still_opens_through_the_environment():
	driver = _wired(_EnvOnlyResource)

	resource = driver._qpu(credential={"user": "alice"})

	assert resource.config is None
	assert resource.env[f"{BACKEND}_QRMI_IBM_QRS_IAM_APIKEY"] == "tok"


def test_each_reservation_opens_with_its_own_settings():
	# What the environment path needed a process-wide lock for. Here each
	# thread carries its own map, so there is nothing to serialize.
	driver = _wired(_Resource)

	def _access(credential=None):
		user = dict(credential or {}).get("user", "operator")
		return {
			"base_url": f"https://{user}.example.org",
			"token": f"{user}-key",
		}

	driver._access = _access
	users = [f"user-{index}" for index in range(8)]
	opened = {}
	errors = []

	def work(user):
		try:
			resource = driver._qpu(credential={"user": user})
			opened[user] = resource.config["QRMI_IBM_QRS_IAM_APIKEY"]
		except Exception as exc:  # pragma: no cover - surfaced by the assert
			errors.append(exc)

	threads = [threading.Thread(target=work, args=(user,)) for user in users]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()

	assert not errors, errors
	assert opened == {user: f"{user}-key" for user in users}


def test_threads_sharing_a_credential_open_one_resource():
	# Per-credential locking still has to hold, because a discarded duplicate
	# would leak the tokio runtime QRMI keeps in a ManuallyDrop.
	driver = _wired(_Resource)
	built = []
	real = _Resource.from_config

	def counting_from_config(alias, resource_type, config):
		built.append(alias)
		return real(alias, resource_type, config)

	driver._qrmi.QuantumResource = types.SimpleNamespace(
		from_config=counting_from_config)

	credential = {"user": "alice"}
	threads = [
		threading.Thread(target=driver._qpu, kwargs={"credential": dict(
			credential)})
		for _ in range(8)
	]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()

	assert len(built) == 1
	assert len(driver._resource_objs) == 1
