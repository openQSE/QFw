# The QRMI driver's vendor timing: behind the flag, IQM only, one task_logs
# request per completed job, parsed into the phases the telemetry records.
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "services"))

from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402
from util import instrumentation  # noqa: E402

TASK_LOGS = """Timeline   :
  2026-10-12 18:39:20.000 [server                  ] created
  2026-10-12 18:39:22.000 [station                 ] execution_started
  2026-10-12 18:39:24.500 [station                 ] execution_ended
  2026-10-12 18:39:25.000 [server                  ] completed
Messages   : (none)
"""


class _Resource:
	def __init__(self, logs=TASK_LOGS):
		self.logs = logs
		self.asked = []

	def task_logs(self, job_id):
		self.asked.append(job_id)
		return self.logs


def _driver(resource, provider="iqm"):
	driver = QrmiDriver({"provider": provider})
	driver._qpu = lambda credential=None: resource
	return driver


def test_nothing_is_asked_of_the_provider_with_the_flag_off(monkeypatch):
	monkeypatch.setattr(instrumentation, "vendor_enabled", lambda: False)
	resource = _Resource()
	assert _driver(resource)._vendor_timing("job-1") == {}
	assert resource.asked == []


def test_a_completed_iqm_job_is_asked_for_its_timeline_once(monkeypatch):
	monkeypatch.setattr(instrumentation, "vendor_enabled", lambda: True)
	recorded = []
	monkeypatch.setattr(
		instrumentation, "record_vendor_timing",
		lambda phases, labels=None: recorded.append(phases))
	resource = _Resource()
	phases = _driver(resource)._vendor_timing("job-1")
	assert resource.asked == ["job-1"]
	assert phases == {"execution": 2.5, "total": 5.0}
	assert recorded == [phases]


def test_other_providers_are_left_alone(monkeypatch):
	monkeypatch.setattr(instrumentation, "vendor_enabled", lambda: True)
	resource = _Resource()
	assert _driver(resource, provider="ibm")._vendor_timing("job-1") == {}
	assert resource.asked == []


def test_a_timeline_that_cannot_be_read_records_nothing(monkeypatch):
	monkeypatch.setattr(instrumentation, "vendor_enabled", lambda: True)
	recorded = []
	monkeypatch.setattr(
		instrumentation, "record_vendor_timing",
		lambda phases, labels=None: recorded.append(phases))
	resource = _Resource(logs="Messages   : (none)\n")
	assert _driver(resource)._vendor_timing("job-1") == {}
	assert recorded == []
