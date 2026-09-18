# Guards the IBM resource environment setup (openQSE/QFw#59 blocker 2). QRMI's
# IBM resources read endpoint, IAM endpoint, API key and service CRN from
# {backend}_QRMI_IBM_<kind>_* at construction, and each IBM service has its own
# variable family. Before this the driver populated only the IQM pair, so an
# IBM resource could be selected but never opened from configuration.
#
# The IAM endpoint belongs to the device, and comes from its iam-endpoint key
# unless QFW_IBM_IAM_ENDPOINT is set. The CRN names an IBM instance, which can
# serve several devices and many users (blocker 3 and its follow-up). It comes
# from the reservation's credential, QFW_IBM_SERVICE_CRN, the user's
# credential DB entry, or the device's service-crn key, in that order.

import pathlib
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers import qrmi_driver as qd  # noqa: E402
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402


ALL_VARS = (
	"ENDPOINT", "IAM_ENDPOINT", "IAM_APIKEY", "SERVICE_CRN",
	"S3_ENDPOINT", "S3_BUCKET", "S3_REGION",
	"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
)

QFW_VARS = (
	"QFW_IBM_SERVICE_CRN", "QFW_IBM_IAM_ENDPOINT",
	"QFW_IBM_S3_ENDPOINT", "QFW_IBM_S3_BUCKET", "QFW_IBM_S3_REGION",
	"QFW_IBM_AWS_ACCESS_KEY_ID", "QFW_IBM_AWS_SECRET_ACCESS_KEY",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
	# These helpers write into os.environ by design, so every test starts from
	# a known-empty set rather than inheriting another test's writes.
	for kind in ("QRS", "QCS", "QS"):
		for suffix in ALL_VARS:
			monkeypatch.delenv(
				f"ibm_torino_QRMI_IBM_{kind}_{suffix}", raising=False)
	for name in QFW_VARS:
		monkeypatch.delenv(name, raising=False)


def _driver(access=None, **descriptor):
	descriptor.setdefault("provider", "ibm")
	driver = QrmiDriver(descriptor)
	resolved = {"base_url": "https://example.org", "token": "tok"}
	if access is not None:
		resolved = access
	driver._access = lambda credential=None: dict(resolved)
	return driver


def test_populates_the_qrs_family(monkeypatch):
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:v1:bluemix:public:x")
	driver = _driver()

	driver._ensure_ibm_env("QRS", "ibm_torino")

	import os
	assert os.environ["ibm_torino_QRMI_IBM_QRS_ENDPOINT"] == \
		"https://example.org"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_APIKEY"] == "tok"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == \
		"crn:v1:bluemix:public:x"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT"] == \
		qd.IBM_DEFAULT_IAM_ENDPOINT


def test_each_service_gets_its_own_family(monkeypatch):
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	_driver()._ensure_ibm_env("QCS", "ibm_torino")

	assert "ibm_torino_QRMI_IBM_QCS_ENDPOINT" in os.environ
	assert "ibm_torino_QRMI_IBM_QRS_ENDPOINT" not in os.environ


def test_without_a_credential_keeps_values_already_set(monkeypatch):
	# With no credential to go on, an operator or a SPANK plugin may have set
	# these, so they are left alone.
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:from-env")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_ENDPOINT", "https://spank")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_IAM_APIKEY", "spank-token")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_SERVICE_CRN", "crn:from-spank")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT", "https://iam")

	_driver()._ensure_ibm_env("QRS", "ibm_torino")

	assert os.environ["ibm_torino_QRMI_IBM_QRS_ENDPOINT"] == "https://spank"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_APIKEY"] == "spank-token"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:from-spank"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT"] == "https://iam"


def test_iam_endpoint_is_overridable(monkeypatch):
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	monkeypatch.setenv("QFW_IBM_IAM_ENDPOINT", "https://iam.private")

	_driver()._ensure_ibm_env("QRS", "ibm_torino")
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT"] == \
		"https://iam.private"


def test_missing_crn_names_what_to_set():
	# The CRN is the one required value with no default, so the error has to
	# say where it comes from, and a site service can only use the config key.
	with pytest.raises(DEFwExecutionError) as excinfo:
		_driver()._ensure_ibm_env("QRS", "ibm_torino")
	message = str(excinfo.value)
	assert "ibm_torino_QRMI_IBM_QRS_SERVICE_CRN" in message
	assert "service-crn" in message
	assert "QFW_IBM_SERVICE_CRN" in message
	assert "SPANK" not in message


def test_unresolvable_device_access_still_reports_what_is_missing(monkeypatch):
	# _access raising must not mask the actionable message. A caller setting
	# the variables directly has no device-access config to resolve.
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	driver = QrmiDriver({"provider": "ibm"})

	def _boom(credential=None):
		raise DEFwExecutionError("no device access configured")

	driver._access = _boom

	with pytest.raises(DEFwExecutionError) as excinfo:
		driver._ensure_ibm_env("QRS", "ibm_torino")
	message = str(excinfo.value)
	assert "ibm_torino_QRMI_IBM_QRS_ENDPOINT" in message
	assert "ibm_torino_QRMI_IBM_QRS_IAM_APIKEY" in message
	assert "no device access configured" not in message


def test_object_storage_is_forwarded_for_quantum_system_only(monkeypatch):
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	monkeypatch.setenv("QFW_IBM_S3_BUCKET", "results")
	monkeypatch.setenv("QFW_IBM_S3_REGION", "us-east")

	_driver()._ensure_ibm_env("QS", "ibm_torino")
	assert os.environ["ibm_torino_QRMI_IBM_QS_S3_BUCKET"] == "results"
	assert os.environ["ibm_torino_QRMI_IBM_QS_S3_REGION"] == "us-east"

	# The other services never read them, so they are not written.
	_driver()._ensure_ibm_env("QRS", "ibm_torino")
	assert "ibm_torino_QRMI_IBM_QRS_S3_BUCKET" not in os.environ


def test_object_storage_absent_does_not_block_construction(monkeypatch):
	# Object storage is environment-only, so an unset bucket must not turn
	# into a required-variable error here; QRMI reports that itself.
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	_driver()._ensure_ibm_env("QS", "ibm_torino")


def test_alias_is_trimmed_at_the_first_comma(monkeypatch):
	# QRMI keys the variables by resource id up to the first comma, matching
	# the IQM path's handling of backend_name,calibration_set_id.
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	_driver()._ensure_ibm_env("QRS", "ibm_torino,extra")
	assert "ibm_torino_QRMI_IBM_QRS_ENDPOINT" in os.environ


# --- reservation credentials ------------------------------------------------
#
# On a long-running service the QPM controller attaches a credential bound to
# the caller's reservation to each circuit, and run_circuit passes it down.
# These variables are process-wide, so that credential has to replace what an
# earlier reservation left behind rather than only fill gaps.

def _driver_resolving_by_user(**descriptor):
	# Resolves each credential to its own endpoint and key, the way a
	# reservation-bound credential resolves through device access.
	descriptor.setdefault("provider", "ibm")
	driver = QrmiDriver(descriptor)

	def _access(credential=None):
		user = dict(credential or {}).get("user", "operator")
		return {
			"base_url": f"https://{user}.example.org",
			"token": f"{user}-key",
		}

	driver._access = _access
	return driver


def test_credential_replaces_an_earlier_reservations_key(monkeypatch):
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	driver = _driver_resolving_by_user()

	driver._ensure_ibm_env("QRS", "ibm_torino", credential={"user": "alice"})
	driver._ensure_ibm_env("QRS", "ibm_torino", credential={"user": "bob"})

	assert os.environ["ibm_torino_QRMI_IBM_QRS_ENDPOINT"] == \
		"https://bob.example.org"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_APIKEY"] == "bob-key"


def test_credential_replaces_the_endpoint_key_and_crn(monkeypatch):
	# With a credential, the endpoint, key and CRN are all set for that
	# reservation, whatever was set before. The CRN can belong to the user, so
	# it is no longer kept. Only the IAM endpoint, which belongs to the device,
	# keeps a value already set.
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:from-env")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_ENDPOINT", "https://preset")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_IAM_APIKEY", "preset-key")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_SERVICE_CRN", "crn:preset")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT", "https://iam")

	_driver_resolving_by_user()._ensure_ibm_env(
		"QRS", "ibm_torino", credential={"user": "alice"})

	assert os.environ["ibm_torino_QRMI_IBM_QRS_ENDPOINT"] == \
		"https://alice.example.org"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_APIKEY"] == "alice-key"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:from-env"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT"] == "https://iam"


def test_unresolvable_credential_fails_instead_of_reusing_a_key(monkeypatch):
	# Unlike the no-credential path, this does not fall back to the
	# missing-variable report. The key already in the environment belongs to
	# some other reservation, so the resolution error has to surface.
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_ENDPOINT", "https://earlier")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_IAM_APIKEY", "earlier-key")
	driver = QrmiDriver({"provider": "ibm"})

	def _boom(credential=None):
		raise DEFwExecutionError("credential cannot be resolved")

	driver._access = _boom

	with pytest.raises(DEFwExecutionError) as excinfo:
		driver._ensure_ibm_env(
			"QRS", "ibm_torino", credential={"user": "alice"})
	assert "credential cannot be resolved" in str(excinfo.value)


def test_credential_without_a_key_does_not_inherit_one(monkeypatch):
	# A credential that resolves no key clears the old one, so the
	# missing-variable report fires instead of the resource opening with it.
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	monkeypatch.setenv("ibm_torino_QRMI_IBM_QRS_IAM_APIKEY", "earlier-key")
	driver = _driver(access={"base_url": "https://example.org", "token": None})

	with pytest.raises(DEFwExecutionError) as excinfo:
		driver._ensure_ibm_env(
			"QRS", "ibm_torino", credential={"user": "alice"})
	assert "ibm_torino_QRMI_IBM_QRS_IAM_APIKEY" in str(excinfo.value)
	assert "ibm_torino_QRMI_IBM_QRS_IAM_APIKEY" not in os.environ


def test_each_reservation_opens_its_resource_with_its_own_key(monkeypatch):
	# The failure this guards, end to end. _qpu opens one QuantumResource per
	# credential and QRMI reads the key from the environment when it does, so
	# filling only missing values handed the first reservation's key to every
	# resource opened after it.
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:x")
	driver = _driver_resolving_by_user(
		provider_device_id="ibm_torino",
		resource_type="IBMQiskitRuntimeService")
	opened = []

	class _Qrmi:
		class ResourceType:
			IBMQiskitRuntimeService = "IBMQiskitRuntimeService"

		@staticmethod
		def QuantumResource(alias, resource_type):
			opened.append(os.environ["ibm_torino_QRMI_IBM_QRS_IAM_APIKEY"])
			return object()

	driver._qrmi = _Qrmi

	driver._qpu(credential={"user": "alice"})
	driver._qpu(credential={"user": "bob"})

	assert opened == ["alice-key", "bob-key"]


# --- the device's service instance ------------------------------------------
#
# A site service is started by the site's service manager, so nothing a user
# or a job exports reaches it. Its CRN has to come from device-access config.

def test_crn_and_iam_endpoint_come_from_the_device():
	import os
	driver = _driver(
		service_crn="crn:from-device",
		iam_endpoint="https://iam.device.example")

	driver._ensure_ibm_env("QRS", "ibm_torino")

	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == \
		"crn:from-device"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT"] == \
		"https://iam.device.example"


def test_device_without_an_iam_endpoint_uses_the_public_one():
	import os
	_driver(service_crn="crn:from-device")._ensure_ibm_env(
		"QRS", "ibm_torino")
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT"] == \
		qd.IBM_DEFAULT_IAM_ENDPOINT


def test_environment_overrides_the_device(monkeypatch):
	# The same precedence _access gives QFW_QC_URL and QFW_API_KEY over the
	# device's url and key, so a job-local service can use another instance.
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:from-env")
	monkeypatch.setenv("QFW_IBM_IAM_ENDPOINT", "https://iam.env.example")
	driver = _driver(
		service_crn="crn:from-device",
		iam_endpoint="https://iam.device.example")

	driver._ensure_ibm_env("QRS", "ibm_torino")

	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:from-env"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_ENDPOINT"] == \
		"https://iam.env.example"


def test_device_crn_holds_across_reservations():
	# Each reservation's credential replaces the endpoint and key, and the
	# device's CRN serves all of them.
	import os
	driver = _driver_resolving_by_user(service_crn="crn:from-device")

	driver._ensure_ibm_env("QRS", "ibm_torino", credential={"user": "alice"})
	driver._ensure_ibm_env("QRS", "ibm_torino", credential={"user": "bob"})

	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == \
		"crn:from-device"
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_APIKEY"] == "bob-key"


def test_site_service_opens_the_resource_with_the_configured_crn(
		monkeypatch, tmp_path):
	# Blocker 3 end to end, with nothing in the environment. The CRN written
	# in device-access config has to be in place when QRMI opens the resource.
	# A real CRN ends in colons, so the file quotes it as YAML requires.
	import os
	from svc_lib_qpm.descriptor import resolve_descriptor
	crn = "crn:v1:bluemix:public:quantum-computing:us-east:a/acct:inst::"
	config = tmp_path / "device-access.yaml"
	config.write_text(
		"qpus:\n"
		"  ibm-torino:\n"
		"    provider: ibm\n"
		"    provider-device-id: ibm_torino\n"
		"    resource-type: IBMQiskitRuntimeService\n"
		"    url: https://quantum.cloud.ibm.com/api/v1\n"
		"    credential-db: qpu-users.json\n"
		f"    service-crn: \"{crn}\"\n",
		encoding="utf-8")
	monkeypatch.setenv("QFW_DEVICE_ACCESS_CFG", str(config))
	monkeypatch.setenv("QFW_QPU_DEVICE_ID", "ibm-torino")
	driver = QrmiDriver(resolve_descriptor())
	driver._access = lambda credential=None: {
		"base_url": "https://quantum.cloud.ibm.com/api/v1", "token": "tok"}
	opened = []

	class _Qrmi:
		class ResourceType:
			IBMQiskitRuntimeService = "IBMQiskitRuntimeService"

		@staticmethod
		def QuantumResource(alias, resource_type):
			opened.append(
				(alias, os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"]))
			return object()

	driver._qrmi = _Qrmi
	driver._qpu()

	assert opened == [("ibm_torino", crn)]


# --- a user's service instance ----------------------------------------------
#
# An IBM instance is shared by its users rather than tied to one device, and
# a user can be assigned to several instances. A user's credential DB entry
# can therefore name the one to run under, which reaches the driver in the
# reservation's credential. The device's service-crn is the default.

def test_each_reservation_runs_under_its_own_instance():
	import os
	driver = _driver_resolving_by_user(service_crn="crn:device")

	driver._ensure_ibm_env(
		"QRS", "ibm_torino",
		credential={"user": "alice", "service_crn": "crn:alice"})
	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:alice"

	driver._ensure_ibm_env(
		"QRS", "ibm_torino",
		credential={"user": "bob", "service_crn": "crn:bob"})
	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:bob"


def test_reservation_without_its_own_instance_uses_the_device_default():
	# Not the previous reservation's instance, which is what keeping the value
	# already set would have given.
	import os
	driver = _driver_resolving_by_user(service_crn="crn:device")

	driver._ensure_ibm_env(
		"QRS", "ibm_torino",
		credential={"user": "alice", "service_crn": "crn:alice"})
	driver._ensure_ibm_env("QRS", "ibm_torino", credential={"user": "bob"})

	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:device"


def test_reservation_with_no_instance_fails_rather_than_inherit_one():
	import os
	driver = _driver_resolving_by_user()

	driver._ensure_ibm_env(
		"QRS", "ibm_torino",
		credential={"user": "alice", "service_crn": "crn:alice"})
	with pytest.raises(DEFwExecutionError) as excinfo:
		driver._ensure_ibm_env(
			"QRS", "ibm_torino", credential={"user": "bob"})

	assert "ibm_torino_QRMI_IBM_QRS_SERVICE_CRN" in str(excinfo.value)
	assert "ibm_torino_QRMI_IBM_QRS_SERVICE_CRN" not in os.environ


def test_credential_instance_outranks_the_environment_and_the_device(
		monkeypatch):
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:from-env")
	driver = _driver_resolving_by_user(service_crn="crn:device")

	driver._ensure_ibm_env(
		"QRS", "ibm_torino",
		credential={"user": "alice", "service_crn": "crn:alice"})

	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:alice"


def test_without_a_credential_the_users_entry_names_the_instance():
	# Introspection outside a reservation resolves the service's own user
	# through device access, and that user's entry can carry a CRN.
	import os
	access = {"base_url": "https://example.org", "token": "tok",
		"service_crn": "crn:user"}

	_driver(access=access, service_crn="crn:device")._ensure_ibm_env(
		"QRS", "ibm_torino")

	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:user"


def test_environment_outranks_the_users_entry_without_a_credential(
		monkeypatch):
	# The same order _access gives QFW_API_KEY over the key found through
	# device access.
	import os
	monkeypatch.setenv("QFW_IBM_SERVICE_CRN", "crn:from-env")
	access = {"base_url": "https://example.org", "token": "tok",
		"service_crn": "crn:user"}

	_driver(access=access, service_crn="crn:device")._ensure_ibm_env(
		"QRS", "ibm_torino")

	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == "crn:from-env"


def test_each_instance_opens_its_own_resource():
	# The same user and key under two instances are two resources. QRMI reads
	# the CRN when it opens one, so a resource cached for the first instance
	# must not be handed out for the second.
	import os
	driver = _driver_resolving_by_user(
		provider_device_id="ibm_torino",
		resource_type="IBMQiskitRuntimeService")
	opened = []

	class _Qrmi:
		class ResourceType:
			IBMQiskitRuntimeService = "IBMQiskitRuntimeService"

		@staticmethod
		def QuantumResource(alias, resource_type):
			opened.append(os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"])
			return object()

	driver._qrmi = _Qrmi
	driver._qpu(credential={"user": "alice", "service_crn": "crn:project-a"})
	driver._qpu(credential={"user": "alice", "service_crn": "crn:project-b"})

	assert opened == ["crn:project-a", "crn:project-b"]


def test_file_credential_provider_carries_the_users_instance(tmp_path):
	# From the credential DB to the environment QRMI reads, with the driver's
	# own device-access resolution. The bound secret carries the user's CRN,
	# and the driver sets it for that reservation over the device default.
	import json
	import os
	import util.qpm.credentials as qpm_credentials
	crn = "crn:v1:bluemix:public:quantum-computing:us-east:a/acct:alice::"
	database = tmp_path / "qpu-users.json"
	database.write_text(json.dumps({"users": {"alice": {
		"enabled": True,
		"devices": {"ibm-torino": {
			"enabled": True, "api_key": "alice-key", "service_crn": crn}},
	}}}), encoding="utf-8")
	device = {
		"device_id": "ibm-torino",
		"provider_device_id": "ibm_torino",
		"provider": "ibm",
		"url": "https://quantum.cloud.ibm.com/api/v1",
		"credential_db": str(database),
	}
	provider = qpm_credentials.FileCredentialProvider(
		str(tmp_path / "device-access.yaml"), device)

	secret = provider.bind({"user": "alice"}).secret
	assert secret["service_crn"] == crn

	QrmiDriver({"provider": "ibm", "service_crn": "crn:device"}) \
		._ensure_ibm_env("QRS", "ibm_torino", credential=secret)
	assert os.environ["ibm_torino_QRMI_IBM_QRS_SERVICE_CRN"] == crn
	assert os.environ["ibm_torino_QRMI_IBM_QRS_IAM_APIKEY"] == "alice-key"


# --- routing ---------------------------------------------------------------

def test_resource_env_routes_by_type():
	driver = _driver()
	iqm_calls, ibm_calls = [], []
	driver._ensure_iqm_isa_env = lambda *a, **k: iqm_calls.append(a)
	driver._ensure_ibm_env = lambda *a, **k: ibm_calls.append(a)

	driver._ensure_resource_env("IQMServer", "default")
	assert len(iqm_calls) == 1 and not ibm_calls

	for type_name, kind in (
			("IBMQiskitRuntimeService", "QRS"),
			("IBMQuantumComputeService", "QCS"),
			("IBMQuantumSystem", "QS")):
		ibm_calls.clear()
		driver._ensure_resource_env(type_name, "ibm_torino")
		assert ibm_calls == [(kind, "ibm_torino")]

	# Pasqal has no device-access mapping yet, so neither helper runs.
	iqm_calls.clear()
	ibm_calls.clear()
	driver._ensure_resource_env("PasqalCloud", "fresnel")
	assert not iqm_calls and not ibm_calls
