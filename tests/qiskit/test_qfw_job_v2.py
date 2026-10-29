"""QFwJobV2 batch compilation, submission, status, and cancellation tests."""

from types import SimpleNamespace

import pytest
from qiskit import QuantumCircuit
from qiskit.providers.jobstatus import JobStatus

from qfw_qiskit import QFwJobV2


class FakeQueue:
	def wait(self, timeout=None):
		return False

	def get(self):
		return ()

	def close(self):
		pass


class FakeContext:
	def __init__(self, *, compile_failure=None, run_failure=None):
		self.compile_failure = compile_failure
		self.run_failure = run_failure
		self.trace = []
		self.statuses = {}
		self.cancelled = []
		self.queue = FakeQueue()

	def import_program(self, circuit):
		self.trace.append(("import", circuit.name))
		return circuit

	def compile(self, route, program):
		index = len([item for item in self.trace if item[0] == "compile"])
		self.trace.append(("compile", program.name))
		if index == self.compile_failure:
			raise RuntimeError("compile failed")
		return SimpleNamespace(payload=f"compiled:{program.name}")

	def create_completion_queue(self, route):
		self.trace.append(("queue", route))
		return self.queue

	def run(self, route, executable, queue, options):
		index = len([item for item in self.trace if item[0] == "run"])
		self.trace.append(("run", executable.payload))
		if index == self.run_failure:
			raise RuntimeError("submission failed")
		op_id = f"op-{index}"
		self.statuses[op_id] = {"state": "queued"}
		return op_id

	def status(self, op_id):
		return self.statuses[op_id]

	def cancel(self, op_id):
		self.cancelled.append(op_id)
		self.statuses[op_id] = {"state": "cancel_requested"}
		return self.statuses[op_id]


def _circuit(name):
	circuit = QuantumCircuit(2, 2, name=name)
	circuit.measure((0, 1), (0, 1))
	return circuit


def _backend(context):
	return SimpleNamespace(
		_context=context,
		_route=object(),
		options=SimpleNamespace(
			shots=128, timeout=None, memory=True, seed_simulator=None),
	)


def test_job_compiles_entire_batch_before_any_remote_operation():
	context = FakeContext(compile_failure=1)
	job = QFwJobV2(_backend(context), (_circuit("a"), _circuit("b")), {})
	with pytest.raises(RuntimeError, match="compile failed"):
		job.submit()
	assert [item[0] for item in context.trace] == (
		["import", "compile", "import", "compile"])
	assert not any(item[0] == "run" for item in context.trace)


def test_job_keeps_accepted_ids_after_immediate_submission_failure():
	context = FakeContext(run_failure=1)
	job = QFwJobV2(_backend(context), (_circuit("a"), _circuit("b")), {})
	job.submit()
	assert job._experiments[0].op_id == "op-0"
	assert job._experiments[1].submission_error is not None
	assert context.cancelled == []
	assert job.status() is JobStatus.ERROR
	assert all("op_id" not in item for item in job.diagnostic_operations())


def test_job_aggregates_status_and_confirms_cancellation_only_at_terminal():
	context = FakeContext()
	job = QFwJobV2(_backend(context), (_circuit("a"), _circuit("b")), {})
	job.submit()
	assert job.status() is JobStatus.QUEUED
	context.statuses["op-0"] = {"state": "running"}
	assert job.status() is JobStatus.RUNNING
	assert job.cancel()
	assert context.cancelled == ["op-0", "op-1"]
	assert job.status() is JobStatus.QUEUED
	context.statuses["op-0"] = {"state": "cancelled"}
	context.statuses["op-1"] = {"state": "cancelled"}
	assert job.status() is JobStatus.CANCELLED


@pytest.mark.parametrize("circuits", ((), "bad", (object(),)))
def test_job_rejects_invalid_circuit_batches(circuits):
	with pytest.raises(TypeError):
		QFwJobV2(_backend(FakeContext()), circuits, {})


def test_job_rejects_unknown_run_options_before_remote_work():
	context = FakeContext()
	with pytest.raises(ValueError, match="unsupported.*option"):
		QFwJobV2(_backend(context), _circuit("a"), {"provider": "iqm"})
	assert context.trace == []
