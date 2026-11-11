import threading
import time

import numpy as np

from svc_fake_iqm_qpm.svc_qrc import QRC as ThreadedQRC
from util import instrumentation
from util.circuit_payload import (
	circuit_payload, load_circuit, logical_circuit_payload)
from util.proxy_sampler.counts import counts_of

PROXY_BACKEND_KIND = "proxy"
BASIS_LETTERS = frozenset("XYZ")


def seeded_generators(seed):
	"""The ideal-draw and noise generators of a job's seed, so one seed fixes both."""
	ideal, noise = np.random.SeedSequence(seed).spawn(2)
	return np.random.default_rng(ideal), np.random.default_rng(noise)


def check_bases(request):
	"""The (physical qubits, bases, shots per basis) of a bases request, validated."""
	physical = [int(qubit) for qubit in request["physical"]]
	bases = [str(basis).upper() for basis in request["bases"]]
	shots = [int(count) for count in request["shots"]]
	if not bases or len(bases) != len(shots):
		raise ValueError("a bases job lists at least one basis, with one shot count each")
	for basis in bases:
		if len(basis) != len(physical) or not set(basis) <= BASIS_LETTERS:
			raise ValueError(
				f"basis {basis!r} is not one X, Y or Z per physical qubit "
				f"({len(physical)} qubits)")
	return physical, bases, shots


class QRC(ThreadedQRC):
	"""Runs each job in a thread of the QPM process with the proxy sampler.

	The threading, completion queue and event push are the fake IQM QRC's. A
	job the sampler refuses, such as a circuit beyond the memory limit, fails
	with the error's type and message in its result.
	"""

	def __init__(self, sampler, target_id, start=True):
		super().__init__(start=start, target_id=target_id)
		self.sampler = sampler
		self._sampling_lock = threading.Lock()

	def sample_job(self, info):
		"""The result payload of one job: a bases job when info["bases"] is set, else one circuit's counts."""
		ideal_rng, noise_rng = seeded_generators(info.get("seed_simulator"))
		circuit = load_circuit(*circuit_payload(info))
		with self._sampling_lock:
			if info.get("bases") is not None:
				return self._bases_payload(circuit, info["bases"], noise_rng)
			logical = logical_circuit_payload(info)
			bits, diagnostics = self.sampler.sample(
				circuit, int(info["num_shots"]), noise_rng,
				logical=None if logical is None else load_circuit(*logical),
				ideal_rng=ideal_rng)
		return {"counts": counts_of(bits), "diagnostics": diagnostics}

	def _bases_payload(self, circuit, request, rng):
		physical, bases, shots = check_bases(request)
		records = self.sampler.sample_bases(circuit, physical, bases, shots, rng)
		return {"counts": {}, "bases": [
			{"basis": basis, "shots": count, "counts": counts_of(bits), "diagnostics": diagnostics}
			for basis, count, (bits, diagnostics) in zip(bases, shots, records)]}

	def _execute(self, circuit, cancel_event=None):
		info = circuit.info
		failure = None
		payload = {}
		with instrumentation.backend_execution(
				circuit, api_path=instrumentation.API_PATH_SIMULATOR,
				device=self.target_id, backend_kind=PROXY_BACKEND_KIND,
				cancel_event=cancel_event):
			circuit.set_launching()
			start_ns = time.time_ns()
			circuit.set_running()
			try:
				payload = self.sample_job(info)
			except Exception as exc:
				failure = exc
			observed_ns = time.time_ns() - start_ns
			if failure is None:
				circuit.set_exec_done()
			else:
				circuit.set_fail()
		result = self._result(circuit, observed_ns, cancelled=False)
		result["provider"] = PROXY_BACKEND_KIND
		if failure is None:
			result["result"] = payload
		else:
			result.update(
				outcome="FAILED", rc=1,
				result={PROXY_BACKEND_KIND: {
					"error": str(failure), "error_type": type(failure).__name__}})
		self._remember_result_metadata(result)
		return result
