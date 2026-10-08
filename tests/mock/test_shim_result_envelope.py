# Guards the result envelope the QRMI/QDMI shim run-queue delivers.
#
# The shim drivers return the qhw-result-v1 record itself, and
# examples/measurement_support.py relies on that. The Qiskit client reads
# counts off the top of the result payload (qfw_job._split_result_payload)
# and keeps a `qhw_result` entry as metadata, the shape the native IQM
# run-queue (svc_iqm_qpm) already delivers. So svc_qrc hoists a driver's
# record into that envelope. Before it did, a Qiskit job through the shim
# received the whole record as its counts, whichever provider ran it.
#
# The driver side is stubbed, so no qiskit, QRMI or QDMI is needed.

import pathlib
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm import svc_qrc  # noqa: E402
from tests.mock.test_shim_cancel import _Circuit, _Event  # noqa: E402


QHW_RECORD = {
	"schema": "qhw-result-v1",
	"provider": "aws",
	"device": {"id": "aws-sv1", "provider": "aws"},
	"job": {"id": "arn:aws:braket:us-east-1:123:quantum-task/abc",
		"status": "completed"},
	"result": {"shots": 10, "num_circuits": 1, "counts": {"1": 10},
		"success": True},
	"metadata": {"source": "qdmi", "via": "mqt.core.qdmi"},
}


class _Frontend:
	# Stands in for the routed driver: returns a fixed output, or raises it.
	def __init__(self, output):
		self.output = output

	def run_circuit(self, circuit, lib=None):
		if isinstance(self.output, Exception):
			raise self.output
		return self.output

	def capability_map(self):
		return {}


class _Listener:
	# Stands in for the event class a client registers; records each push.
	def __init__(self):
		self.events = []

	def put(self, event):
		self.events.append(event)
		return True


def _shim_qrc(monkeypatch, output):
	monkeypatch.setattr(
		svc_qrc, "resolve_descriptor", lambda: {"libraries": []})
	monkeypatch.setattr(
		svc_qrc, "Frontend", lambda drivers, descriptor: _Frontend(output))
	monkeypatch.setattr(svc_qrc, "Event", _Event)
	return svc_qrc.QRC(start=False)


def test_a_qhw_record_is_hoisted_into_the_client_envelope(monkeypatch):
	result = _shim_qrc(monkeypatch, QHW_RECORD).sync_run(_Circuit())

	assert result["rc"] == 0
	assert result["result"] == {
		"counts": {"1": 10},
		"qhw_result": QHW_RECORD,
	}
	# The record travels whole, not rebuilt field by field.
	assert result["result"]["qhw_result"] is QHW_RECORD


def test_a_record_without_counts_still_gets_an_envelope(monkeypatch):
	record = dict(QHW_RECORD, result={"shots": 0})

	result = _shim_qrc(monkeypatch, record).sync_run(_Circuit())

	assert result["result"] == {"counts": {}, "qhw_result": record}


def test_a_plain_result_passes_through_untouched(monkeypatch):
	# Not every driver output is a qhw record. A plain counts dict is what
	# the client reads already, so it is left alone.
	output = {"counts": {"00": 10}}

	result = _shim_qrc(monkeypatch, output).sync_run(_Circuit())

	assert result["result"] is output


def test_another_qhw_schema_passes_through_untouched(monkeypatch):
	output = {"schema": "qhw-device-v1", "device": {"id": "x"}}

	result = _shim_qrc(monkeypatch, output).sync_run(_Circuit())

	assert result["result"] is output


@pytest.mark.parametrize("output", [None, "done", 7, ["1", "0"]])
def test_a_non_dict_output_passes_through_untouched(output):
	assert svc_qrc._result_envelope(output) is output


def test_a_failed_circuit_keeps_the_failure_shape(monkeypatch):
	qrc = _shim_qrc(
		monkeypatch, DEFwExecutionError("QDMI submit_job failed: boom"))
	circuit = _Circuit()

	qrc.async_run(circuit)
	qrc.threads[-1].join(timeout=5)

	result = qrc.read_cq("cid-1")
	assert result["rc"] == -1
	assert result["result"]["counts"] == {}
	assert result["result"]["shim"]["error_type"] == "DEFwExecutionError"
	assert "qhw_result" not in result["result"]
	assert "failed" in circuit.states


def test_the_envelope_reaches_a_pushed_event(monkeypatch):
	# async_run pushes the result to a registered listener instead of
	# queueing it. The pushed payload carries the same envelope.
	qrc = _shim_qrc(monkeypatch, QHW_RECORD)
	listener = _Listener()
	qrc.register_event_notification(
		{"class": listener, "evtype": "circ_result"})

	qrc.async_run(_Circuit())
	qrc.threads[-1].join(timeout=5)

	assert len(listener.events) == 1
	event = listener.events[0]
	assert event.get_evtype() == "circ_result"
	assert event.get_event()["result"] == {
		"counts": {"1": 10},
		"qhw_result": QHW_RECORD,
	}
	assert qrc.read_cq("cid-1") is None
