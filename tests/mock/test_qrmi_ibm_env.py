# Guards the IBM resource environment setup (openQSE/QFw#59 blocker 2). QRMI's
# IBM resources read endpoint, IAM endpoint, API key and service CRN from
# {backend}_QRMI_IBM_<kind>_* at construction, and each IBM service has its own
# variable family. Before this the driver populated only the IQM pair, so an
# IBM resource could be selected but never opened from configuration.
#
# The CRN and IAM endpoint have no device-access field yet (blocker 3), so they
# come from the environment. That is process-wide rather than per-device, which
# these tests pin down so the limitation is visible rather than implied.

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


def test_never_overrides_values_already_set(monkeypatch):
	# Inside a reservation the SPANK plugin owns these.
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
	# The CRN is the one required value with no config source, so the error
	# has to say where it comes from.
	with pytest.raises(DEFwExecutionError) as excinfo:
		_driver()._ensure_ibm_env("QRS", "ibm_torino")
	message = str(excinfo.value)
	assert "ibm_torino_QRMI_IBM_QRS_SERVICE_CRN" in message
	assert "QFW_IBM_SERVICE_CRN" in message


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
