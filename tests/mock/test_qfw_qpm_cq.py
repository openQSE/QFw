# Guards the completion-queue lookups on UTIL_QPM. peek_cq(cid) used to call
# self.qrc.peak_cq() with no argument, dropping the cid and always returning the
# head of the queue -- unlike its sibling read_cq(cid), which forwards it. These
# tests pin both methods to forward the cid.
#
# UTIL_QPM.__init__ wires up DEFw host resources, so the methods are exercised on
# an instance built with __new__ (only qrc and the module's qpm_initialized flag
# are needed here).

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

import util.qpm.util_qpm as util_qpm  # noqa: E402


class RecordingQRC:
	def __init__(self, result=None):
		self.result = result
		self.peek_cid = "UNSET"
		self.read_cid = "UNSET"

	def peak_cq(self, cid=None):
		self.peek_cid = cid
		return self.result

	def read_cq(self, cid=None):
		self.read_cid = cid
		return self.result


def _make_qpm(qrc):
	qpm = util_qpm.UTIL_QPM.__new__(util_qpm.UTIL_QPM)
	qpm.qrc = qrc
	qpm.all_results = []
	return qpm


def test_peek_cq_forwards_cid(monkeypatch):
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	qrc = RecordingQRC(result={"cid": "cid-7"})
	qpm = _make_qpm(qrc)

	out = qpm.peek_cq("cid-7")

	assert qrc.peek_cid == "cid-7"
	assert out == {"cid": "cid-7"}


def test_peek_cq_raises_in_progress_when_absent(monkeypatch):
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	qrc = RecordingQRC(result=None)
	qpm = _make_qpm(qrc)

	with pytest.raises(util_qpm.DEFwInProgress):
		qpm.peek_cq("cid-x")

	# The cid still reaches the queue (so a targeted peek does not fall back to
	# the head of the queue).
	assert qrc.peek_cid == "cid-x"


def test_peek_cq_requires_initialization(monkeypatch):
	monkeypatch.setattr(util_qpm, "qpm_initialized", False)
	qpm = _make_qpm(RecordingQRC())

	with pytest.raises(util_qpm.DEFwNotReady):
		qpm.peek_cq("cid-x")


def test_read_cq_forwards_cid(monkeypatch):
	# Sibling guard: read_cq already forwarded the cid; keep the two consistent.
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	qrc = RecordingQRC(result={"cid": "cid-9"})
	qpm = _make_qpm(qrc)

	out = qpm.read_cq("cid-9")

	assert qrc.read_cid == "cid-9"
	assert out == {"cid": "cid-9"}
	assert qpm.all_results == [{"cid": "cid-9"}]
