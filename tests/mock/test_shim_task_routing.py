# Guards where the QRMI/QDMI shim reads a task's timing and metadata from.
#
# get_task_timing and get_task_metadata are execution calls, and the
# Frontend pins those to the device's execution owner, QRMI by default. A
# circuit can run through the other library (info["lib"], or a Qiskit job's
# lib option), and the owner never saw it. So the QPM asked QRMI about a
# circuit QDMI ran, and the lookup failed. The shim QRC now remembers the
# library each circuit ran through and sends its lookups there.
#
# The real Frontend routes between stub drivers, so no QRMI or QDMI is
# needed.

import pathlib
import sys


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from svc_lib_qpm import svc_qrc  # noqa: E402
from svc_lib_qpm.descriptor import DEFAULT_CAPS  # noqa: E402
from tests.mock.test_shim_cancel import _Circuit, _Event  # noqa: E402


class _Driver:
	CAPABILITIES = frozenset({
		"run_circuit", "get_task_timing", "get_task_metadata"})

	def __init__(self, name):
		self.name = name
		self.ran = []

	def implements(self, call):
		return call in self.CAPABILITIES

	def run_circuit(self, circuit):
		self.ran.append(circuit.get_cid())
		return {"counts": {"1": 1}}

	def get_task_timing(self, cid=None):
		return {"library": self.name, "cid": cid}

	def get_task_metadata(self, cid=None):
		return {"library": self.name, "cid": cid}


def _shim_qrc(monkeypatch):
	drivers = {"qrmi": _Driver("qrmi"), "qdmi": _Driver("qdmi")}
	monkeypatch.setattr(svc_qrc, "resolve_descriptor", lambda: {
		"id": "ornl-iqm-20q",
		"libraries": ["qrmi", "qdmi"],
		"execution_owner": "qrmi",
		"caps": {call: list(libs) for call, libs in DEFAULT_CAPS.items()},
	})
	monkeypatch.setattr(svc_qrc, "_DRIVER_FACTORY", {
		name: (lambda descriptor, driver=driver: driver)
		for name, driver in drivers.items()})
	monkeypatch.setattr(svc_qrc, "Event", _Event)
	return svc_qrc.QRC(start=False), drivers


def _run(qrc, cid, lib=None):
	circuit = _Circuit(cid)
	if lib:
		circuit.info["lib"] = lib
	assert qrc.sync_run(circuit)["rc"] == 0
	return circuit


def test_a_qdmi_run_is_looked_up_in_qdmi(monkeypatch):
	qrc, drivers = _shim_qrc(monkeypatch)
	_run(qrc, "cid-q", lib="qdmi")

	assert drivers["qdmi"].ran == ["cid-q"]
	assert qrc.get_task_timing("cid-q")["library"] == "qdmi"
	assert qrc.get_task_metadata("cid-q")["library"] == "qdmi"


def test_a_default_run_is_looked_up_in_the_execution_owner(monkeypatch):
	qrc, drivers = _shim_qrc(monkeypatch)
	_run(qrc, "cid-r")

	assert drivers["qrmi"].ran == ["cid-r"]
	assert qrc.get_task_metadata("cid-r")["library"] == "qrmi"


def test_each_cid_keeps_its_own_library(monkeypatch):
	qrc, _ = _shim_qrc(monkeypatch)
	_run(qrc, "cid-1", lib="qdmi")
	_run(qrc, "cid-2", lib="qrmi")
	_run(qrc, "cid-3", lib="qdmi")

	assert [qrc.get_task_metadata(cid)["library"]
		for cid in ("cid-1", "cid-2", "cid-3")] == ["qdmi", "qrmi", "qdmi"]


def test_an_explicit_lib_still_wins(monkeypatch):
	qrc, _ = _shim_qrc(monkeypatch)
	_run(qrc, "cid-q", lib="qdmi")

	assert qrc.get_task_metadata("cid-q", lib="qrmi")["library"] == "qrmi"


def test_a_cid_the_qrc_did_not_run_keeps_the_old_routing(monkeypatch):
	qrc, _ = _shim_qrc(monkeypatch)

	assert qrc.get_task_metadata("cid-unknown")["library"] == "qrmi"


def test_the_qrc_remembers_a_bounded_number_of_runs(monkeypatch):
	monkeypatch.setattr(svc_qrc, "TASK_LIBRARY_LIMIT", 2)
	qrc, _ = _shim_qrc(monkeypatch)
	for cid in ("cid-0", "cid-1", "cid-2"):
		_run(qrc, cid, lib="qdmi")

	# The oldest is forgotten and falls back to the execution owner.
	assert qrc.get_task_metadata("cid-0")["library"] == "qrmi"
	assert qrc.get_task_metadata("cid-2")["library"] == "qdmi"
