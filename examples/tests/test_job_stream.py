#!/usr/bin/env python3
"""
A stream of Qiskit jobs through one QFw backend, for load and for watching.

Every other example runs one circuit and stops. This one keeps submitting:
a configurable mix of GHZ and random circuits over a range of qubit counts,
at a chosen interval, from one or more concurrent workers, for a number of
jobs or a length of time. It is what feeds the telemetry dashboards during a
demonstration, and a plain way to put a QPM under a steady load.

Each worker owns a QFwBackend, so concurrent workers are concurrent jobs at
the QPM and the queue and dispatch hops have something to show. The jobs all
run under the reservation the wrapper made (qfw_job_stream.sh), which sizes
its walltime and task count from the same options.

Progress goes to stdout one line per job, prefixed QFW_JOB_STREAM, and the
summary is the usual qfw-example-result-v1 record at the end.
"""
import argparse
import random
import resource
import statistics
import sys
import threading
import time
import traceback

from qfw_example_report import emit_result

EXAMPLE = "job-stream"
PROGRESS_PREFIX = "QFW_JOB_STREAM"
CIRCUIT_KINDS = ("ghz", "random")
OUTCOME_COMPLETED = "completed"
OUTCOME_FAILED = "failed"


def parse_qubit_range(text):
	"""'2-5' -> (2, 5); '4' -> (4, 4). The bounds are inclusive."""
	text = str(text).strip()
	if "-" in text:
		low, high = text.split("-", 1)
	else:
		low = high = text
	low = int(low)
	high = int(high)
	if low < 1 or high < low:
		raise ValueError(f"qubit range must be MIN-MAX with 1 <= MIN <= MAX: {text!r}")
	return low, high


def parse_kinds(text):
	kinds = []
	for item in str(text).split(","):
		item = item.strip().lower()
		if not item:
			continue
		if item == "mixed":
			kinds.extend(CIRCUIT_KINDS)
			continue
		if item not in CIRCUIT_KINDS:
			raise ValueError(
				f"unknown circuit kind {item!r}; choose from "
				f"{', '.join(CIRCUIT_KINDS)} or mixed")
		kinds.append(item)
	if not kinds:
		raise ValueError("at least one circuit kind is required")
	return kinds


def plan_jobs(count, kinds, qubit_range, rng):
	"""The (kind, qubits) of each job, cycling the kinds and drawing the size."""
	low, high = qubit_range
	plan = []
	for index in range(count):
		plan.append((kinds[index % len(kinds)], rng.randint(low, high)))
	return plan


def build_circuit(kind, qubits, rng):
	"""A measured circuit of the given kind. Imports qiskit when first used."""
	from qiskit import QuantumCircuit

	if kind == "ghz":
		circuit = QuantumCircuit(qubits, name=f"ghz-{qubits}")
		circuit.h(0)
		for qubit in range(qubits - 1):
			circuit.cx(qubit, qubit + 1)
	elif kind == "random":
		from qiskit.circuit.random import random_circuit

		depth = max(2, qubits)
		circuit = random_circuit(
			qubits, depth, max_operands=2, measure=False,
			seed=rng.randrange(2**31))
		circuit.name = f"random-{qubits}x{depth}"
	else:
		raise ValueError(f"unknown circuit kind {kind!r}")
	circuit.measure_all()
	return circuit


def qfw_backend_factory(backend_name):
	"""One QFwBackend per worker, the way the other examples build theirs."""
	def factory():
		from qfw_qiskit import QFwBackend

		return QFwBackend(provider=backend_name)
	return factory


def process_cpu_seconds():
	"""This process's user plus system CPU time so far, for a cost per job."""
	usage = resource.getrusage(resource.RUSAGE_SELF)
	return usage.ru_utime + usage.ru_stime


def summarize(records, started, ended, cpu_seconds=None):
	"""The metrics of a run: counts, latency quantiles, throughput and CPU."""
	elapsed = max(ended - started, 1e-9)
	latencies = sorted(
		r["seconds"] for r in records if r["outcome"] == OUTCOME_COMPLETED)
	completed = len(latencies)
	failed = sum(1 for r in records if r["outcome"] != OUTCOME_COMPLETED)
	by_kind = {}
	for record in records:
		entry = by_kind.setdefault(record["kind"], {"completed": 0, "failed": 0})
		entry["completed" if record["outcome"] == OUTCOME_COMPLETED else "failed"] += 1

	def quantile(fraction):
		if not latencies:
			return None
		index = min(len(latencies) - 1, int(round(fraction * (len(latencies) - 1))))
		return latencies[index]

	return {
		"jobs": len(records),
		"completed": completed,
		"failed": failed,
		"elapsed_seconds": elapsed,
		"jobs_per_minute": 60.0 * len(records) / elapsed,
		"latency_seconds": {
			"mean": statistics.fmean(latencies) if latencies else None,
			"p50": quantile(0.5),
			"p95": quantile(0.95),
			"max": latencies[-1] if latencies else None,
		},
		"by_kind": by_kind,
		"process_cpu_seconds": cpu_seconds,
		"process_cpu_seconds_per_job": (
			cpu_seconds / len(records) if cpu_seconds is not None and records else None),
	}


class _Stream:
	"""The shared state of one run: the plan, a cursor over it, the records."""

	def __init__(self, plan, duration, stop_on_error, clock):
		self.plan = plan
		self.duration = duration
		self.stop_on_error = stop_on_error
		self.clock = clock
		self.started = clock()
		self.records = []
		self.stopped = False
		self._next = 0
		self._lock = threading.Lock()

	def take(self):
		"""The next job's (index, kind, qubits), or None when the run is over."""
		with self._lock:
			if self.stopped:
				return None
			if self.duration and self.clock() - self.started >= self.duration:
				return None
			if self.plan is not None:
				if self._next >= len(self.plan):
					return None
				kind, qubits = self.plan[self._next]
			else:
				kind, qubits = self.endless(self._next)
			self._next += 1
			return self._next - 1, kind, qubits

	def endless(self, index):
		raise NotImplementedError

	def record(self, entry):
		with self._lock:
			self.records.append(entry)
			if entry["outcome"] != OUTCOME_COMPLETED and self.stop_on_error:
				self.stopped = True


class _EndlessStream(_Stream):
	"""A duration-bounded run draws jobs until the time is up."""

	def __init__(self, kinds, qubit_range, rng, duration, stop_on_error, clock):
		super().__init__(None, duration, stop_on_error, clock)
		self.kinds = kinds
		self.qubit_range = qubit_range
		self.rng = rng

	def endless(self, index):
		low, high = self.qubit_range
		return self.kinds[index % len(self.kinds)], self.rng.randint(low, high)


def run_job(backend, circuit, shots, run_options):
	"""Submit one circuit and wait for its result; the counts, for the log."""
	job = backend.run(circuit, shots=shots, **run_options)
	result = job.result()
	return result.get_counts()


# Backends are built one at a time. Building one is a burst of DEFw RPCs
# (directory lookup, connect, event registration), and several of those
# bursts at once from one agent process time out.
_BACKEND_BUILD_LOCK = threading.Lock()


def _worker(worker_id, stream, backend_factory, circuit_factory, shots,
		run_options, interval, rng_seed, log):
	rng = random.Random(rng_seed)
	backend = None
	while True:
		item = stream.take()
		if item is None:
			return
		index, kind, qubits = item
		started = stream.clock()
		entry = {
			"index": index, "worker": worker_id, "kind": kind, "qubits": qubits,
			"outcome": OUTCOME_COMPLETED, "seconds": 0.0, "error": None,
		}
		try:
			if backend is None:
				with _BACKEND_BUILD_LOCK:
					backend = backend_factory()
			circuit = circuit_factory(kind, qubits, rng)
			counts = run_job(backend, circuit, shots, run_options)
			entry["seconds"] = stream.clock() - started
			entry["distinct_outcomes"] = len(counts) if hasattr(counts, "__len__") else None
		except Exception as error:  # one failed job must not end the stream
			entry["seconds"] = stream.clock() - started
			entry["outcome"] = OUTCOME_FAILED
			entry["error"] = f"{type(error).__name__}: {error}"
			log(traceback.format_exc().rstrip())
		stream.record(entry)
		log(f"{PROGRESS_PREFIX} job={index + 1} worker={worker_id} kind={kind} "
			f"qubits={qubits} outcome={entry['outcome']} seconds={entry['seconds']:.3f}"
			+ (f" error={entry['error']}" if entry["error"] else ""))
		if interval > 0:
			time.sleep(interval)


def run_stream(backend_factory, run_options, kinds, qubit_range, shots, jobs,
		duration, interval, workers, seed, stop_on_error,
		circuit_factory=build_circuit, clock=time.monotonic, log=print):
	"""
	Run the stream and return (records, started, ended). jobs > 0 runs that
	many jobs (a fixed plan, reproducible from the seed); jobs == 0 runs
	until duration seconds have passed.
	"""
	rng = random.Random(seed)
	if jobs > 0:
		stream = _Stream(plan_jobs(jobs, kinds, qubit_range, rng), duration,
			stop_on_error, clock)
	elif duration > 0:
		stream = _EndlessStream(kinds, qubit_range, rng, duration,
			stop_on_error, clock)
	else:
		raise ValueError("either --jobs or --duration must be positive")
	threads = []
	for worker_id in range(1, max(1, workers) + 1):
		thread = threading.Thread(
			target=_worker,
			args=(worker_id, stream, backend_factory, circuit_factory, shots,
				run_options, interval, rng.randrange(2**31), log),
			name=f"job-stream-{worker_id}", daemon=True)
		threads.append(thread)
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()
	return stream.records, stream.started, clock()


def parse_args(argv=None):
	parser = argparse.ArgumentParser(
		description="Stream Qiskit jobs through a QFw backend.")
	parser.add_argument("--backend", default="nwqsim",
		help="QFw provider backend name (default: nwqsim)")
	parser.add_argument("--jobs", type=int, default=20,
		help="number of jobs, 0 to run for --duration instead (default: 20)")
	parser.add_argument("--duration", type=float, default=0.0,
		help="stop after this many seconds (default: no limit)")
	parser.add_argument("--interval", type=float, default=1.0,
		help="seconds each worker pauses between its jobs (default: 1.0)")
	parser.add_argument("--workers", type=int, default=1,
		help="concurrent workers, each with its own backend (default: 1)")
	parser.add_argument("--qubits", default="2-5",
		help="qubit count or inclusive MIN-MAX range per job (default: 2-5)")
	parser.add_argument("--shots", type=int, default=1024)
	parser.add_argument("--circuits", default="ghz,random",
		help="comma-separated kinds cycled through: ghz, random, or mixed "
		"(default: ghz,random)")
	parser.add_argument("--seed", type=int, default=None,
		help="seed for the job plan and the random circuits")
	parser.add_argument("--stop-on-error", action="store_true",
		help="end the stream at the first failed job")
	parser.add_argument("--tolerate-failures", action="store_true",
		help="exit 0 even when some jobs failed, as long as one completed")
	args = parser.parse_args(argv)
	args.qubit_range = parse_qubit_range(args.qubits)
	args.kinds = parse_kinds(args.circuits)
	if args.jobs < 0 or args.duration < 0 or args.interval < 0 or args.workers < 1:
		parser.error("--jobs, --duration and --interval must not be negative; --workers must be at least 1")
	if args.jobs == 0 and args.duration <= 0:
		parser.error("--jobs 0 needs a positive --duration")
	if args.seed is None:
		args.seed = random.randrange(2**31)
	return args


def main(argv=None, backend_factory=None, run_options=None,
		circuit_factory=build_circuit):
	args = parse_args(argv)
	if backend_factory is None:
		backend_factory = qfw_backend_factory(args.backend)
	if run_options is None:
		from qfw_example_context import qfw_reservation_options

		run_options = qfw_reservation_options()
	print(f"{PROGRESS_PREFIX} start backend={args.backend} jobs={args.jobs or 'until-duration'} "
		f"duration={args.duration or 'none'} interval={args.interval} workers={args.workers} "
		f"qubits={args.qubits} shots={args.shots} circuits={','.join(args.kinds)} seed={args.seed}")
	cpu_before = process_cpu_seconds()
	records, started, ended = run_stream(
		backend_factory, run_options, args.kinds, args.qubit_range, args.shots,
		args.jobs, args.duration, args.interval, args.workers, args.seed,
		args.stop_on_error, circuit_factory=circuit_factory)
	# The client's own CPU over the stream, backends and all, which is the
	# other half of what instrumentation can cost.
	metrics = summarize(records, started, ended,
		cpu_seconds=process_cpu_seconds() - cpu_before)
	ok = metrics["failed"] == 0 or (args.tolerate_failures and metrics["completed"] > 0)
	emit_result(
		EXAMPLE,
		status="ok" if ok else "error",
		parameters={
			"backend": args.backend, "jobs": args.jobs, "duration": args.duration,
			"interval": args.interval, "workers": args.workers,
			"qubits": args.qubits, "shots": args.shots,
			"circuits": args.kinds, "seed": args.seed,
		},
		metrics=metrics,
		details={"jobs": records},
	)
	return 0 if ok else 1


if __name__ == "__main__":
	sys.exit(main())
