from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, TypeVar

import numpy as np
from numpy.typing import NDArray
from qiskit import QuantumCircuit

from .circuits import active_qubits, circuit_digest
from .errors import AerMemoryRefused, FfsimMemoryRefused
from .fermionic import FermionSpace, NotFermionic, ffsim_module, ffsim_program
from .gates import BARRIER_DELAY, FFSIM_GATES
from .pauli_frames import Bits, Floats
from .rotation_states import (
	aer_result,
	aer_statevector,
	rotation_circuit,
	rotation_program,
)

logger = logging.getLogger(__name__)

Ints = NDArray[np.int64]
Amplitudes = NDArray[np.complex128]
Cached = TypeVar("Cached")


def remember(cache: OrderedDict[str, Cached], key: str, value: Cached, limit: int) -> None:
	cache[key] = value
	cache.move_to_end(key)
	while len(cache) > limit:
		cache.popitem(last=False)


def measured_map(circuit: Any) -> list[tuple[int, int]]:
	last_into: dict[int, int] = {}
	for instruction in circuit.data:
		if instruction.operation.name == "measure":
			last_into[circuit.find_bit(instruction.clbits[0]).index] = circuit.find_bit(instruction.qubits[0]).index
	return [(qubit, clbit) for clbit, qubit in last_into.items()]


def on_active_qubits(circuit: Any) -> tuple[Any, list[int]]:
	active = active_qubits(circuit)
	index = {q: k for k, q in enumerate(active)}
	compact = QuantumCircuit(len(active), circuit.num_clbits)
	for instruction in circuit.data:
		if instruction.operation.name in BARRIER_DELAY:
			continue
		qubits = [index[circuit.find_bit(q).index] for q in instruction.qubits]
		clbits = [circuit.find_bit(c).index for c in instruction.clbits]
		compact.append(instruction.operation, qubits, clbits)
	return compact, active


def gate_passes(circuit: Any) -> int:
	return len(circuit.data) << int(circuit.num_qubits)


def born_probabilities(amplitudes: Amplitudes) -> Floats:
	probabilities = np.abs(np.asarray(amplitudes)) ** 2
	probabilities /= probabilities.sum()
	return probabilities


MPS = "matrix_product_state"
MIB = 1 << 20
AMPLITUDE_BYTES = np.dtype(np.complex128).itemsize
FLOAT_BYTES = np.dtype(np.float64).itemsize
INDEX_BYTES = np.dtype(np.int64).itemsize
PROBABILITY_BYTES = FLOAT_BYTES
OUTCOME_ARRAY_BYTES = PROBABILITY_BYTES + 2 * INDEX_BYTES


def _held_bytes(value: Any) -> int:
	return int(getattr(value, "vec", value).nbytes)


@dataclass
class MemoryLimit:
	max_memory_mb: int
	caches: tuple[OrderedDict[str, Any], ...]

	@property
	def limit_bytes(self) -> int:
		return self.max_memory_mb * MIB

	def held_bytes(self) -> int:
		return sum(_held_bytes(value) for cache in self.caches for value in cache.values())

	def make_room(self, needed: int, keep: str | None = None) -> bool:
		while needed + self.held_bytes() > self.limit_bytes:
			evictable = [cache for cache in self.caches if any(key != keep for key in cache)]
			if not evictable:
				return False
			cache = max(evictable, key=lambda c: sum(_held_bytes(value) for value in c.values()))
			del cache[next(key for key in cache if key != keep)]
		return True

	def refusal(self, computation: str, needed: int) -> str:
		held = self.held_bytes()
		cached = f" beside {-(-held // MIB)} MiB of cached states" if held else ""
		return f"{computation} needs {-(-needed // MIB)} MiB{cached}"


def _is_ffsim_native(circuit: Any) -> bool:
	names = {instruction.operation.name for instruction in circuit.data}
	lowered = {name.lower() for name in names}
	return bool(lowered & FFSIM_GATES) or any("jw" in name.lower() and name.lower() != "measure" for name in names)


def _mid_circuit_measure(circuit: Any) -> bool:
	measured: set[int] = set()
	for instruction in circuit.data:
		qubits = [circuit.find_bit(q).index for q in instruction.qubits]
		if instruction.operation.name == "measure":
			measured.update(qubits)
		elif instruction.operation.name not in BARRIER_DELAY and measured.intersection(qubits):
			return True
	return any(i.operation.name in ("if_else", "reset", "while_loop", "for_loop", "switch_case")
			   for i in circuit.data)


def _occupation_words(norb: int, nelec: int | tuple[int, int], picks: Ints) -> Ints:
	import ffsim

	if isinstance(nelec, int):
		strings = ffsim.addresses_to_strings(np.arange(ffsim.dim(norb, nelec)), norb, nelec,
											 bitstring_type=ffsim.BitstringType.INT)
		return np.asarray(strings)[picks].astype(np.int64)
	n_alpha, n_beta = nelec
	dim_b = ffsim.dim(norb, n_beta)
	alpha_strings = np.asarray(ffsim.addresses_to_strings(np.arange(ffsim.dim(norb, n_alpha)), norb, n_alpha,
														  bitstring_type=ffsim.BitstringType.INT))
	beta_strings = np.asarray(ffsim.addresses_to_strings(np.arange(dim_b), norb, n_beta,
														 bitstring_type=ffsim.BitstringType.INT))
	alpha = alpha_strings[picks // dim_b].astype(np.int64)
	beta = beta_strings[picks % dim_b].astype(np.int64)
	return alpha | (beta << norb)


@dataclass(frozen=True)
class IdealDraw:
	circuit: Any
	measured: list[tuple[int, int]]
	logical: Any
	shots: int
	rng: np.random.Generator

	@classmethod
	def of(cls, circuit: Any, logical: Any, shots: int, rng: np.random.Generator) -> IdealDraw:
		approximate = (getattr(circuit, "metadata", None) or {}).get("approximate", False)
		return cls(circuit, measured_map(circuit), None if approximate else logical, shots, rng)

	@property
	def known(self) -> Any:
		return (getattr(self.circuit, "metadata", None) or {}).get("ideal_clbits")

	def picks(self, probabilities: Floats) -> Ints:
		picks: Ints = self.rng.choice(len(probabilities), size=self.shots, p=probabilities)
		return picks

	def logical_bits(self, words: Ints) -> Bits:
		bits = np.zeros((self.shots, self.logical.num_clbits), dtype=bool)
		for qubit, clbit in measured_map(self.logical):
			bits[:, clbit] = (words >> qubit) & 1
		return bits


class IdealSource:
	def __init__(self, successor: IdealSource | None) -> None:
		self.successor = successor

	def outcomes(self, draw: IdealDraw) -> tuple[Bits, str]:
		if self.successor is None:
			raise LookupError("no ideal source samples this circuit")
		return self.successor.outcomes(draw)


class KnownOutcomes(IdealSource):
	def outcomes(self, draw: IdealDraw) -> tuple[Bits, str]:
		known = draw.known
		if known is None:
			return super().outcomes(draw)
		return np.tile(np.asarray(known, dtype=bool), (draw.shots, 1)), "known"


class FfsimStates(IdealSource):
	def __init__(self, successor: IdealSource, states: OrderedDict[str, Any], cache_limit: int, max_memory_mb: int,
				 stats: dict[str, int], memory: MemoryLimit | None = None) -> None:
		super().__init__(successor)
		self.states, self.cache_limit, self.stats = states, cache_limit, stats
		self.max_memory_mb = max_memory_mb
		self.memory = memory or MemoryLimit(max_memory_mb, (states,))

	def outcomes(self, draw: IdealDraw) -> tuple[Bits, str]:
		if draw.logical is None or not _is_ffsim_native(draw.logical):
			return super().outcomes(draw)
		if ffsim_module() is None:
			logger.info("FfsimStates: ffsim is not installed; the next ideal source samples the circuit")
			return super().outcomes(draw)
		key = circuit_digest(draw.logical)
		state = self._state(draw.logical, key)
		if state is None:
			return super().outcomes(draw)
		needed = state.vec.size * PROBABILITY_BYTES
		if not self.memory.make_room(needed, keep=key):
			raise FfsimMemoryRefused(self.memory.refusal("sampling the ffsim state", needed), self.max_memory_mb)
		self.stats["ffsim"] += 1
		picks = draw.picks(born_probabilities(state.vec))
		logger.debug("FfsimStates.outcomes: qubits=%d shots=%d", draw.logical.num_qubits, draw.shots)
		return draw.logical_bits(_occupation_words(state.norb, state.nelec, picks)), "ffsim"

	def _state(self, logical: Any, key: str) -> Any:
		state = self.states.get(key)
		if state is not None:
			return state
		try:
			program = ffsim_program(logical)
		except NotFermionic as reason:
			logger.warning("FfsimStates: ffsim cannot simulate the logical circuit, %s; the next ideal source "
						   "samples it", reason)
			return None
		self._make_room(program.space)
		try:
			state = ffsim_module().qiskit.final_state_vector(
				program.circuit, norb=program.space.norb, nelec=program.space.nelec)
		except ValueError as reason:
			logger.warning("FfsimStates: ffsim refused the logical circuit, %s; the next ideal source samples it",
						   reason)
			return None
		remember(self.states, key, state, self.cache_limit)
		return state

	def _make_room(self, space: FermionSpace) -> None:
		needed = ffsim_module().dim(space.norb, space.nelec) * AMPLITUDE_BYTES
		if not self.memory.make_room(needed):
			state = f"the ffsim state vector of {space.norb} orbitals and {space.nelec} electrons"
			raise FfsimMemoryRefused(self.memory.refusal(state, needed), self.max_memory_mb)


class RotationPrograms(IdealSource):
	def __init__(self, successor: IdealSource, states: OrderedDict[str, Any], cache_limit: int, max_memory_mb: int,
				 stats: dict[str, int], memory: MemoryLimit | None = None) -> None:
		super().__init__(successor)
		self.states, self.cache_limit, self.stats = states, cache_limit, stats
		self.max_memory_mb = max_memory_mb
		self.memory = memory or MemoryLimit(max_memory_mb, (states,))
		self.not_rotation: set[str] = set()

	def outcomes(self, draw: IdealDraw) -> tuple[Bits, str]:
		probabilities = None if draw.logical is None else self._probabilities(draw.logical, draw.circuit)
		if probabilities is None:
			return super().outcomes(draw)
		bits = draw.logical_bits(draw.picks(probabilities))
		self.stats["rotations"] = self.stats.get("rotations", 0) + 1
		return bits, "pauli-rotations"

	def _probabilities(self, logical: Any, circuit: Any) -> Floats | None:
		if _mid_circuit_measure(logical):
			return None
		key = "rotations:" + circuit_digest(logical)
		probabilities: Floats | None = self.states.get(key)
		if probabilities is not None:
			self.stats["prefix_hits"] += 1
			return probabilities
		if key in self.not_rotation:
			return None
		ops = rotation_program(logical)
		if ops is None:
			self.not_rotation.add(key)
			return None
		compact, _ = on_active_qubits(circuit)
		program = rotation_circuit(logical.num_qubits, ops)
		if gate_passes(program) >= gate_passes(compact):
			return None
		needed = (AMPLITUDE_BYTES + PROBABILITY_BYTES) << program.num_qubits
		if not self.memory.make_room(needed):
			logger.info("RotationPrograms: %s, over the %d MiB memory limit",
						self.memory.refusal("the rotation program's state", needed), self.max_memory_mb)
			return None
		try:
			probabilities = born_probabilities(aer_statevector(program, self.max_memory_mb))
		except AerMemoryRefused as refusal:
			logger.info("RotationPrograms: %s", refusal)
			return None
		remember(self.states, key, probabilities, self.cache_limit)
		return probabilities


class AerStatevectors(IdealSource):
	def __init__(self, states: OrderedDict[str, Any], cache_limit: int, max_memory_mb: int,
				 stats: dict[str, int], memory: MemoryLimit | None = None) -> None:
		super().__init__(None)
		self.states, self.cache_limit, self.stats = states, cache_limit, stats
		self.max_memory_mb = max_memory_mb
		self.memory = memory or MemoryLimit(max_memory_mb, (states,))

	def statevector(self, compact: Any, working_bytes_per_amplitude: int = 0) -> Amplitudes:
		unitary = compact.remove_final_measurements(inplace=False)
		key = circuit_digest(unitary)
		vector: Amplitudes | None = self.states.get(key)
		needed = (working_bytes_per_amplitude + (AMPLITUDE_BYTES if vector is None else 0)) << unitary.num_qubits
		if not self.memory.make_room(needed, keep=key):
			raise AerMemoryRefused(f"a statevector of {unitary.num_qubits} qubits", self.max_memory_mb,
								   self.memory.refusal("it", needed))
		if vector is None:
			vector = aer_statevector(unitary, self.max_memory_mb)
			remember(self.states, key, vector, self.cache_limit)
		else:
			self.stats["prefix_hits"] += 1
		return vector

	def outcomes(self, draw: IdealDraw) -> tuple[Bits, str]:
		circuit = draw.circuit
		compact, active = on_active_qubits(circuit)
		if _mid_circuit_measure(circuit):
			return self._shots(compact, draw)
		try:
			vector = self.statevector(compact, OUTCOME_ARRAY_BYTES)
		except AerMemoryRefused as refusal:
			logger.info("AerStatevectors: %s; sampled by matrix_product_state", refusal)
			return self._shots(compact, draw, MPS)
		index = {q: i for i, q in enumerate(active)}
		probabilities = born_probabilities(vector)
		states = np.arange(len(probabilities), dtype=np.int64)
		words = np.zeros(len(probabilities), dtype=np.int64)
		for qubit, clbit in draw.measured:
			words |= ((states >> index[qubit]) & 1) << clbit
		weights = np.bincount(words, weights=probabilities)
		picks = draw.picks(weights / weights.sum())
		bits = np.zeros((draw.shots, circuit.num_clbits), dtype=bool)
		for _, clbit in draw.measured:
			bits[:, clbit] = (picks >> clbit) & 1
		self.stats["statevector"] += 1
		return bits, "statevector"

	def _shots(self, compact: Any, draw: IdealDraw, method: str = "statevector") -> tuple[Bits, str]:
		seed = int(draw.rng.integers(2**31))
		try:
			result = aer_result(compact, method, self.max_memory_mb, shots=draw.shots, seed_simulator=seed)
		except AerMemoryRefused as refusal:
			if method == MPS:
				raise
			logger.info("AerStatevectors: %s; sampled by matrix_product_state", refusal)
			method = MPS
			result = aer_result(compact, method, self.max_memory_mb, shots=draw.shots, seed_simulator=seed)
		num_clbits = draw.circuit.num_clbits
		bits = np.zeros((draw.shots, num_clbits), dtype=bool)
		row = 0
		for key, count in sorted(result.get_counts(0).items()):
			word = key.replace(" ", "")[::-1]
			bits[row: row + count] = np.array([c == "1" for c in word[:num_clbits]], dtype=bool)
			row += count
		draw.rng.shuffle(bits)
		self.stats["mps" if method == MPS else "statevector"] += 1
		return bits, method
