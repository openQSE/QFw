from __future__ import annotations

from typing import Any

import numpy as np

from .counts import counts_of
from .sampler import ProxySampler

BASIS_LETTERS = frozenset("XYZ")


def seeded_generators(seed: int | None) -> tuple[np.random.Generator, np.random.Generator]:
	ideal, noise = np.random.SeedSequence(seed).spawn(2)
	return np.random.default_rng(ideal), np.random.default_rng(noise)


def check_bases(request: dict[str, Any]) -> tuple[list[int], list[str], list[int]]:
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


def sample_job(sampler: ProxySampler, circuit: Any, shots: int, seed: int | None, logical: Any = None,
			   bases: dict[str, Any] | None = None) -> dict[str, Any]:
	ideal_rng, noise_rng = seeded_generators(seed)
	if bases is not None:
		physical, letters, counts = check_bases(bases)
		records = sampler.sample_bases(circuit, physical, letters, counts, noise_rng)
		return {"counts": {}, "bases": [
			{"basis": basis, "shots": count, "counts": counts_of(bits), "diagnostics": diagnostics}
			for basis, count, (bits, diagnostics) in zip(letters, counts, records)]}
	bits, diagnostics = sampler.sample(circuit, shots, noise_rng, logical=logical, ideal_rng=ideal_rng)
	return {"counts": counts_of(bits), "diagnostics": diagnostics}
