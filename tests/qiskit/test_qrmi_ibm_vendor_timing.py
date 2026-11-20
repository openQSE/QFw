import pathlib
import sys
import types

import pytest

from util import instrumentation

SERVICES = pathlib.Path(__file__).resolve().parents[2] / "services"
ENDPOINT = "https://quantum.cloud.ibm.com/api/v1"
API_KEY = "ibm-api-key"
SERVICE_CRN = "crn:v1:bluemix:public:quantum-computing:us-east:a/acct:inst::"
CREATED = "2026-11-20T10:00:00.250Z"
RUNNING = "2026-11-20T10:02:30.750Z"
FINISHED = "2026-11-20T10:02:41Z"
QUEUE_SECONDS = 150.5


class _Runtime:
	def __init__(self, timestamps=None, fails=False):
		self.timestamps = timestamps
		self.fails = fails
		self.opened = []
		self.asked = []

	def service(self, **kwargs):
		self.opened.append(kwargs)
		return self

	def job(self, job_id):
		self.asked.append(job_id)
		return self

	def metrics(self):
		if self.fails:
			raise RuntimeError("metrics unavailable")
		return {"timestamps": self.timestamps}


@pytest.fixture
def driver_class(monkeypatch):
	for name, path in (
			("svc_lib_qpm", SERVICES / "svc_lib_qpm"),
			("svc_lib_qpm.drivers", SERVICES / "svc_lib_qpm/drivers")):
		if name not in sys.modules:
			package = types.ModuleType(name)
			package.__path__ = [str(path)]
			monkeypatch.setitem(sys.modules, name, package)
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	return QrmiDriver


@pytest.fixture
def recorded(monkeypatch):
	for kind in ("QRS", "QCS", "QS"):
		for suffix in ("ENDPOINT", "IAM_ENDPOINT", "IAM_APIKEY", "SERVICE_CRN"):
			monkeypatch.delenv(f"ibm_fez_QRMI_IBM_{kind}_{suffix}", raising=False)
	for name in ("QFW_IBM_SERVICE_CRN", "QFW_IBM_IAM_ENDPOINT"):
		monkeypatch.delenv(name, raising=False)
	monkeypatch.setattr(instrumentation, "vendor_enabled", lambda: True)
	phases = []
	monkeypatch.setattr(
		instrumentation, "record_vendor_timing",
		lambda timing, labels=None: phases.append(timing))
	return phases


@pytest.fixture
def driver(driver_class, monkeypatch):
	def make(runtime, resource_type="IBMQiskitRuntimeService"):
		monkeypatch.setattr(
			"qiskit_ibm_runtime.QiskitRuntimeService", runtime.service)
		opened = driver_class({
			"provider": "ibm", "resource_type": resource_type,
			"service_crn": SERVICE_CRN})
		opened._access = lambda credential=None: {
			"base_url": ENDPOINT, "token": API_KEY}
		opened._qc_alias = lambda credential=None: "ibm_fez"
		return opened
	return make


def test_a_runtime_job_reports_its_queue_time(driver, recorded):
	runtime = _Runtime(
		{"created": CREATED, "running": RUNNING, "finished": FINISHED})

	phases = driver(runtime)._vendor_timing("job-1")

	assert phases == {"queue": QUEUE_SECONDS}
	assert recorded == [phases]
	assert runtime.asked == ["job-1"]
	(opened,) = runtime.opened
	assert opened["token"] == API_KEY
	assert opened["instance"] == SERVICE_CRN
	assert opened["url_resolver"]("https://cloud.ibm.com", SERVICE_CRN) == ENDPOINT


@pytest.mark.parametrize(
	"resource_type", ["IBMQuantumComputeService", "IBMQuantumSystem"])
def test_the_other_ibm_services_are_not_asked(
		driver, recorded, resource_type):
	runtime = _Runtime({"created": CREATED, "running": RUNNING})

	assert driver(runtime, resource_type)._vendor_timing(
		"job-1") == {}
	assert runtime.opened == []


def test_a_job_that_never_ran_records_no_queue_time(driver, recorded):
	runtime = _Runtime({"created": CREATED, "running": None})

	assert driver(runtime)._vendor_timing("job-1") == {}
	assert recorded == []


def test_a_failed_metrics_request_records_nothing(driver, recorded):
	runtime = _Runtime(fails=True)

	assert driver(runtime)._vendor_timing("job-1") == {}
	assert recorded == []
