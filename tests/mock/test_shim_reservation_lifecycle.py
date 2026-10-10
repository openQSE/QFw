# Guards what the QRMI/QDMI shim keeps per reservation and per task.
#
# A reservation's session is opened with its bound credential, so it holds
# that user's token. The QPM controller calls the QRC's
# evict_reservation_client when the reservation is released, cancelled or
# expires, and each driver drops what it opened for it, as the native IQM
# QPM drops its client. Task timing and metadata are kept per cid and looked
# up by cid, so one user's call never reads another user's job.
#
# The provider side is stubbed, so no QRMI or QDMI is needed. The QDMI
# driver's session and lookup cases are in test_qdmi_profiles.py, beside its
# fakes.

import pathlib
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm import svc_qrc  # noqa: E402
from svc_lib_qpm.drivers.base_driver import (  # noqa: E402
	JobRecords, reservation_cache_key)
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402


DESCRIPTOR = {"id": "ornl-iqm-20q", "provider": "iqm",
	      "provider-device-id": "default"}


# --- job records --------------------------------------------------------------

def test_job_records_are_found_by_cid():
	jobs = JobRecords("QRMI")
	jobs.put({"cid": "cid-1", "id": "job-1"})
	jobs.put({"cid": "cid-2", "id": "job-2"})

	assert jobs.get("cid-1")["id"] == "job-1"
	assert jobs.get("cid-2")["id"] == "job-2"


def test_a_job_lookup_needs_a_cid():
	jobs = JobRecords("QRMI")
	jobs.put({"cid": "cid-1", "id": "job-1"})

	with pytest.raises(DEFwExecutionError, match="need the task's cid"):
		jobs.get(None)


def test_an_unknown_cid_does_not_name_anyone_elses():
	jobs = JobRecords("QRMI")
	jobs.put({"cid": "someone-elses", "id": "job-1"})

	with pytest.raises(DEFwExecutionError) as excinfo:
		jobs.get("mine")
	assert "someone-elses" not in str(excinfo.value)


def test_job_records_keep_the_newest():
	jobs = JobRecords("QRMI", limit=2)
	for index in range(3):
		jobs.put({"cid": f"cid-{index}", "id": f"job-{index}"})

	with pytest.raises(DEFwExecutionError):
		jobs.get("cid-0")
	assert jobs.get("cid-2")["id"] == "job-2"


# --- the QRMI driver ----------------------------------------------------------

def test_qrmi_keys_a_reservation_resource_by_its_reservation():
	key = QrmiDriver._credential_cache_key(
		QrmiDriver(DESCRIPTOR),
		{"url": "https://qc.example.org", "api_key": "k", "user": "alice",
		 "reservation_id": 7})

	assert key == reservation_cache_key(7)


def test_qrmi_drops_an_ended_reservation_resource():
	driver = QrmiDriver(DESCRIPTOR)
	ended = reservation_cache_key(7)
	other = reservation_cache_key(8)
	for key in (ended, other, ("default",)):
		driver._resource_objs[key] = object()
		driver._target_cache[key] = {}
	driver._resource_lock(ended)

	assert driver.evict_reservation(7) is True

	assert ended not in driver._resource_objs
	assert ended not in driver._target_cache
	assert ended not in driver._resource_locks
	assert set(driver._resource_objs) == {other, ("default",)}
	assert driver.evict_reservation(7) is False


def test_qrmi_task_lookups_find_each_cid():
	driver = QrmiDriver(DESCRIPTOR)
	driver._jobs.put({"cid": "cid-1", "id": "job-1", "status": "completed"})
	driver._jobs.put({"cid": "cid-2", "id": "job-2", "status": "completed"})

	assert driver.get_task_metadata("cid-1")["job_id"] == "job-1"
	assert driver.get_task_timing("cid-2")["job_id"] == "job-2"
	with pytest.raises(DEFwExecutionError, match="need the task's cid"):
		driver.get_task_metadata(None)


# --- the shim QRC -------------------------------------------------------------

class _Driver:
	def __init__(self, name, holds):
		self.name = name
		self.holds = set(holds)
		self.evicted = []

	def implements(self, call):
		return False

	def evict_reservation(self, reservation_id):
		self.evicted.append(reservation_id)
		if reservation_id in self.holds:
			self.holds.discard(reservation_id)
			return True
		return False


def _shim_qrc(monkeypatch, drivers):
	monkeypatch.setattr(svc_qrc, "resolve_descriptor", lambda: {
		"id": "ornl-iqm-20q", "libraries": list(drivers)})
	monkeypatch.setattr(svc_qrc, "_DRIVER_FACTORY", {
		name: (lambda descriptor, driver=driver: driver)
		for name, driver in drivers.items()})
	return svc_qrc.QRC(start=False)


def test_the_shim_qrc_evicts_from_every_driver(monkeypatch):
	# UTIL_QPM wires the QRC's evict_reservation_client as the controller's
	# provider credential evictor. The shim QRC never had one, so nothing
	# a reservation opened was ever dropped.
	qrmi = _Driver("qrmi", holds=[7])
	qdmi = _Driver("qdmi", holds=[7, 8])
	qrc = _shim_qrc(monkeypatch, {"qrmi": qrmi, "qdmi": qdmi})

	assert qrc.evict_reservation_client(7) is True
	assert qrc.evict_reservation_client(8) is True
	assert qrc.evict_reservation_client(9) is False
	assert qrmi.evicted == [7, 8, 9]
	assert qdmi.evicted == [7, 8, 9]


# --- UTIL_QPM's provider task details -----------------------------------------

def test_a_task_the_controller_does_not_know_asks_no_provider():
	# An unknown task id comes back UNKNOWN, with no cid and no reservation
	# check. Asking the provider with no cid handed back its last job, which
	# could be another user's: the native IQM QPM falls back to its latest
	# cid, and the shim drivers returned their last job.
	from util.qpm.util_qpm import UTIL_QPM

	class Controller:
		def task_status_for_qtask_id(
				self, task_id, reservation_id=None,
				require_reservation=False):
			return {"outcome": "UNKNOWN", "lifecycle_state": "unknown",
				"qtask_id": task_id}

	class QRC:
		def get_task_timing(self, cid):
			raise AssertionError("the provider was asked")

		def get_task_metadata(self, cid):
			raise AssertionError("the provider was asked")

	qpm = UTIL_QPM.__new__(UTIL_QPM)
	qpm.controller = Controller()
	qpm.qrc = QRC()

	unavailable = {"available": False, "reason": "no-provider-task"}
	assert qpm.get_task_timing(
		reservation_id=23, task_id=999)["timing"] == unavailable
	assert qpm.get_task_metadata(
		reservation_id=23, task_id=999)["provider_metadata"] == unavailable
