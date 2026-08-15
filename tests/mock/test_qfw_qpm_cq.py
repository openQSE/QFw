# Guards the completion-queue lookups on UTIL_QPM. These tests pin both methods
# to forward the cid to the admission/scheduler controller.
#
# UTIL_QPM.__init__ wires up DEFw host resources, so the methods are exercised on
# an instance built with __new__.

import pathlib
import queue
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

import util.qpm.util_qpm as util_qpm  # noqa: E402


class RecordingController:
	def __init__(self, result=None):
		self.result = result
		self.peek_args = None
		self.read_args = None
		self.retry_count = 0

	def peek_completion(self, **kwargs):
		self.peek_args = kwargs
		return self.result

	def read_completion(self, **kwargs):
		self.read_args = kwargs
		return self.result

	def retry_pending_capacity(self):
		self.retry_count += 1


def _make_qpm(controller):
	qpm = util_qpm.UTIL_QPM.__new__(util_qpm.UTIL_QPM)
	qpm.controller = controller
	qpm.all_results = []
	qpm.oor_queue = queue.Queue()
	return qpm


def test_peek_cq_forwards_cid(monkeypatch):
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	controller = RecordingController(result={
		"completion_ready": True,
		"cid": "cid-7",
	})
	qpm = _make_qpm(controller)

	out = qpm.peek_cq("cid-7")

	assert controller.peek_args == {
		"reservation_id": None,
		"cid": "cid-7",
		"operation": "peek_cq",
	}
	assert out["cid"] == "cid-7"


def test_peek_cq_raises_in_progress_when_absent(monkeypatch):
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	controller = RecordingController(result={
		"completion_ready": False,
		"cid": "cid-x",
	})
	qpm = _make_qpm(controller)

	result = qpm.peek_cq("cid-x")

	assert result["completion_ready"] is False
	assert controller.peek_args["cid"] == "cid-x"


def test_peek_cq_requires_initialization(monkeypatch):
	monkeypatch.setattr(util_qpm, "qpm_initialized", False)
	qpm = _make_qpm(RecordingController())

	with pytest.raises(util_qpm.DEFwNotReady):
		qpm.peek_cq("cid-x")


def test_read_cq_forwards_cid(monkeypatch):
	# Sibling guard: read_cq already forwarded the cid; keep the two consistent.
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	controller = RecordingController(result={
		"completion_ready": True,
		"cid": "cid-9",
	})
	qpm = _make_qpm(controller)

	out = qpm.read_cq("cid-9")

	assert controller.retry_count == 1
	assert controller.read_args == {
		"reservation_id": None,
		"cid": "cid-9",
		"operation": "read_cq",
	}
	assert out["cid"] == "cid-9"
	assert qpm.all_results == [out]
