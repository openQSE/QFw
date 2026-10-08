"""
The job stream example's planning, running and reporting, with a fake
backend. The circuits are built by a stub factory, so nothing here needs
qiskit beyond the stub the mock suite installs.
"""
import importlib.util
import json
import math
import threading
from pathlib import Path

import pytest


def _load():
	path = Path(__file__).resolve().parents[2] / "examples" / "tests" / "test_job_stream.py"
	spec = importlib.util.spec_from_file_location("qfw_job_stream_example", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


stream = _load()


class _FakeResult:
	def __init__(self, counts):
		self._counts = counts

	def get_counts(self, circuit=None):
		return self._counts


class _FakeJob:
	def __init__(self, counts):
		self._counts = counts

	def result(self):
		return _FakeResult(self._counts)


class _FakeBackend:
	"""Records what it ran; fails the circuits whose name says so."""

	def __init__(self, log, barrier=None):
		self.log = log
		self.barrier = barrier

	def run(self, circuit, shots=None, **options):
		if self.barrier is not None:
			# Hold the first job of every worker until all workers have one,
			# so the test sees them concurrent rather than one worker racing
			# through the whole plan.
			self.barrier.wait(timeout=5)
			self.barrier = None
		self.log.append((threading.current_thread().name, circuit, shots, dict(options)))
		if "fail" in circuit:
			raise RuntimeError(f"{circuit} refused")
		return _FakeJob({"0" * 3: shots})


def _fake_circuit(kind, qubits, rng):
	return f"{kind}-{qubits}"


def _failing_circuit(kind, qubits, rng):
	return f"{kind}-{qubits}-fail" if kind == "random" else f"{kind}-{qubits}"


def test_qubit_range_and_kinds_parse():
	assert stream.parse_qubit_range("2-5") == (2, 5)
	assert stream.parse_qubit_range("4") == (4, 4)
	for bad in ("0-3", "5-2", "x"):
		with pytest.raises(ValueError):
			stream.parse_qubit_range(bad)
	assert stream.parse_kinds("ghz,random") == ["ghz", "random"]
	assert stream.parse_kinds("mixed") == ["ghz", "random"]
	with pytest.raises(ValueError):
		stream.parse_kinds("qft")


def test_plan_is_reproducible_and_cycles_the_kinds():
	import random

	first = stream.plan_jobs(6, ["ghz", "random"], (2, 5), random.Random(7))
	second = stream.plan_jobs(6, ["ghz", "random"], (2, 5), random.Random(7))
	assert first == second
	assert [kind for kind, _ in first] == ["ghz", "random"] * 3
	assert all(2 <= qubits <= 5 for _, qubits in first)


def test_stream_runs_every_job_once_with_the_reservation(monkeypatch):
	log = []
	records, started, ended = stream.run_stream(
		lambda: _FakeBackend(log), {"reservation_id": 41, "token": "t"},
		["ghz", "random"], (2, 3), 16, jobs=5, duration=0, interval=0,
		workers=1, seed=1, stop_on_error=False,
		circuit_factory=_fake_circuit, log=lambda line: None)
	assert len(records) == 5
	assert [r["index"] for r in records] == [0, 1, 2, 3, 4]
	assert all(r["outcome"] == "completed" for r in records)
	assert len(log) == 5
	assert all(options == {"reservation_id": 41, "token": "t"} and shots == 16
		for _, _, shots, options in log)
	assert ended >= started


def test_concurrent_workers_share_the_plan_and_each_own_a_backend():
	log = []
	backends = []
	barrier = threading.Barrier(3)

	def factory():
		backend = _FakeBackend(log, barrier)
		backends.append(backend)
		return backend

	records, _, _ = stream.run_stream(
		factory, {}, ["ghz"], (2, 2), 8, jobs=12, duration=0, interval=0,
		workers=3, seed=3, stop_on_error=False,
		circuit_factory=_fake_circuit, log=lambda line: None)
	assert sorted(r["index"] for r in records) == list(range(12))
	assert len(backends) == 3
	assert {r["worker"] for r in records} <= {1, 2, 3}
	assert len({thread for thread, *_ in log}) == 3


def test_a_failed_job_is_recorded_and_the_stream_goes_on():
	lines = []
	records, _, _ = stream.run_stream(
		lambda: _FakeBackend([]), {}, ["ghz", "random"], (2, 2), 8, jobs=4,
		duration=0, interval=0, workers=1, seed=5, stop_on_error=False,
		circuit_factory=_failing_circuit, log=lines.append)
	outcomes = [r["outcome"] for r in records]
	assert outcomes == ["completed", "failed", "completed", "failed"]
	assert "RuntimeError: random-2-fail refused" in records[1]["error"]
	assert any(line.startswith(stream.PROGRESS_PREFIX) and "outcome=failed" in line
		for line in lines)


def test_stop_on_error_ends_the_stream_at_the_first_failure():
	records, _, _ = stream.run_stream(
		lambda: _FakeBackend([]), {}, ["ghz", "random"], (2, 2), 8, jobs=10,
		duration=0, interval=0, workers=1, seed=5, stop_on_error=True,
		circuit_factory=_failing_circuit, log=lambda line: None)
	assert [r["outcome"] for r in records] == ["completed", "failed"]


def test_duration_bounds_an_endless_stream():
	clock = {"now": 100.0}

	def tick():
		clock["now"] += 0.5
		return clock["now"]

	records, started, ended = stream.run_stream(
		lambda: _FakeBackend([]), {}, ["ghz"], (2, 2), 8, jobs=0, duration=5.0,
		interval=0, workers=1, seed=9, stop_on_error=False,
		circuit_factory=_fake_circuit, clock=tick, log=lambda line: None)
	assert 1 <= len(records) <= 10
	assert ended - started >= 5.0


def test_summary_has_counts_quantiles_and_throughput():
	records = [
		{"kind": "ghz", "outcome": "completed", "seconds": s}
		for s in (0.5, 0.7, 0.9, 1.1, 3.0)
	] + [{"kind": "random", "outcome": "failed", "seconds": 0.1}]
	metrics = stream.summarize(records, started=0.0, ended=30.0)
	assert metrics["jobs"] == 6 and metrics["completed"] == 5 and metrics["failed"] == 1
	assert math.isclose(metrics["jobs_per_minute"], 12.0)
	assert metrics["latency_seconds"]["p50"] == 0.9
	assert metrics["latency_seconds"]["p95"] == 3.0
	assert metrics["latency_seconds"]["max"] == 3.0
	assert math.isclose(metrics["latency_seconds"]["mean"], 1.24)
	assert metrics["by_kind"] == {
		"ghz": {"completed": 5, "failed": 0},
		"random": {"completed": 0, "failed": 1},
	}
	assert metrics["process_cpu_seconds"] is None
	assert stream.summarize([], 0.0, 1.0)["latency_seconds"]["p50"] is None
	with_cpu = stream.summarize(records, 0.0, 30.0, cpu_seconds=0.3)
	assert math.isclose(with_cpu["process_cpu_seconds_per_job"], 0.05)


def test_main_emits_the_example_record_and_exit_status(capsys, tmp_path, monkeypatch):
	result_file = tmp_path / "result.jsonl"
	monkeypatch.setenv("QFW_EXAMPLE_RESULT_FILE", str(result_file))

	rc = stream.main(
		["--backend", "fake-iqm", "--jobs", "4", "--interval", "0", "--seed", "2",
		 "--qubits", "2-3", "--shots", "8"],
		backend_factory=lambda: _FakeBackend([]), run_options={"reservation_id": 7},
		circuit_factory=_failing_circuit)
	assert rc == 1
	record = json.loads(result_file.read_text().splitlines()[-1])
	assert record["example"] == "job-stream"
	assert record["status"] == "error"
	assert record["metrics"]["completed"] == 2 and record["metrics"]["failed"] == 2
	assert record["metrics"]["process_cpu_seconds"] >= 0.0
	assert record["metrics"]["process_cpu_seconds_per_job"] >= 0.0
	assert record["parameters"]["seed"] == 2
	assert len(record["details"]["jobs"]) == 4
	out = capsys.readouterr().out
	assert f"{stream.PROGRESS_PREFIX} start backend=fake-iqm" in out

	rc = stream.main(
		["--jobs", "4", "--interval", "0", "--tolerate-failures"],
		backend_factory=lambda: _FakeBackend([]), run_options={},
		circuit_factory=_failing_circuit)
	assert rc == 0


def test_main_rejects_an_unbounded_stream():
	with pytest.raises(SystemExit):
		stream.parse_args(["--jobs", "0"])
	with pytest.raises(SystemExit):
		stream.parse_args(["--workers", "0"])
