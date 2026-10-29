"""QFwJobV2 result ordering, timeout retention, and shutdown tests."""

from types import SimpleNamespace

import pytest
from qiskit import QuantumCircuit
from qiskit.providers.exceptions import JobError, JobTimeoutError

import libquantum as lq
import qfw_qiskit
from qfw_qiskit import QFwBackendV2, QFwJobV2


class FakeQueue:
	def __init__(self):
		self.batches = []
		self.closed = False
		self.wait_calls = 0
		self.get_calls = 0

	def wait(self, timeout=None):
		self.wait_calls += 1
		return bool(self.batches)

	def get(self):
		self.get_calls += 1
		return tuple(self.batches.pop(0))

	def close(self):
		self.closed = True


class FakeContext:
	def __init__(self):
		self.queue = FakeQueue()
		self.statuses = {}
		self.submissions = []

	def import_program(self, circuit):
		return circuit

	def compile(self, route, program):
		return SimpleNamespace(payload=program.name)

	def create_completion_queue(self, route):
		return self.queue

	def run(self, route, executable, queue, options):
		op_id = f"op-{len(self.submissions)}"
		self.submissions.append((op_id, executable.payload))
		self.statuses[op_id] = {"state": "running"}
		return op_id

	def status(self, op_id):
		return self.statuses[op_id]


def _backend(context):
	return SimpleNamespace(
		_context=context,
		_route=object(),
		options=SimpleNamespace(shots=2, timeout=None),
		name="qfw_v2",
		backend_version="0.1",
	)


def _circuit(name):
	circuit = QuantumCircuit(2, 2, name=name, metadata={"identity": name})
	circuit.measure((0, 1), (0, 1))
	return circuit


def _completion(op_id, result=None, error=None):
	return SimpleNamespace(op_id=op_id, result=result, error=error)


def test_result_correlates_out_of_order_and_caches_terminal_result():
	context = FakeContext()
	job = QFwJobV2(_backend(context), (_circuit("first"), _circuit("second")), {})
	job.submit()
	context.queue.batches.append((
		_completion("op-1", {
			"experiment_id": "second",
			"counts": {"11": 2},
			"memory": ["11", "11"],
			"shots": 2,
			"statevector": [0, 0, 0, 1],
			"qhw_result": {"calibration": "two"},
		}),
		_completion("op-0", {
			"experiment_id": "first",
			"counts": {"00": 1, "01": 1},
			"shots": 2,
		}),
	))
	result = job.result(timeout=1)
	assert [item.header["name"] for item in result.results] == [
		"first", "second"]
	assert result.get_counts(0) == {"00": 1, "01": 1}
	assert result.get_memory(1) == ["11", "11"]
	assert result.data(1)["statevector"] == [0, 0, 0, 1]
	assert result.results[1].metadata["qhw_result"] == {"calibration": "two"}
	assert context.queue.closed
	get_calls = context.queue.get_calls
	assert job.result() is result
	assert context.queue.get_calls == get_calls


def test_result_timeout_retains_live_queue_for_retry():
	context = FakeContext()
	job = QFwJobV2(_backend(context), _circuit("retry"), {})
	job.submit()
	with pytest.raises(JobTimeoutError):
		job.result(timeout=0)
	assert not context.queue.closed
	assert job._experiments[0].op_id == "op-0"
	context.queue.batches.append((_completion("op-0", {
		"experiment_id": "retry",
		"counts": {"00": 2},
		"shots": 2,
	}),))
	assert job.result(timeout=1).success


@pytest.mark.parametrize("payload,match", (
	({"counts": {"000": 2}, "shots": 2}, "bit width"),
	({"counts": {"00": 1}, "shots": 2}, "shot count"),
	({"experiment_id": "wrong", "counts": {"00": 2}}, "identity"),
	({"statevector": [1, 0]}, "dimension"),
))
def test_result_rejects_unqualified_payload_shapes(payload, match):
	context = FakeContext()
	job = QFwJobV2(_backend(context), _circuit("expected"), {})
	job.submit()
	context.queue.batches.append((_completion("op-0", payload),))
	with pytest.raises(JobError, match=match):
		job.result(timeout=1)
	assert not context.queue.closed


def test_shutdown_is_idempotent_and_never_cancels(monkeypatch):
	backend = object.__new__(QFwBackendV2)
	backend._closed = False
	backend._context = object()
	backend._finalize_timeout = 9
	backend._jobs = set()
	calls = []
	result = lq.FinalizeResult(lq.FinalizeStatus.TIMED_OUT, ("op-7",))
	monkeypatch.setattr(
		lq,
		"finalize",
		lambda context, timeout: calls.append((context, timeout)) or result,
	)
	assert backend.shutdown() is result
	assert backend.shutdown() is result
	assert calls == [(backend._context, 9)]
	assert result.outstanding_op_ids == ("op-7",)


def test_legacy_and_v2_backends_are_distinct_without_fallback():
	assert qfw_qiskit._EXPORTS["QFwBackend"] == (
		"qfw_simulator", "QFwBackend")
	assert qfw_qiskit._EXPORTS["QFwBackendV2"] == (
		"qfw_backend_v2", "QFwBackendV2")
