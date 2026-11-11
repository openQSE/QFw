"""Calibrated proxy sampler: the ideal distribution (ffsim, Aer statevector or MPS) with calibrated Pauli-frame noise
and asymmetric readout error."""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray
from qiskit import ClassicalRegister

from .calibration import Calibration, error_budget
from .circuits import circuit_digest
from .ideal_sources import (
	AerStatevectors,
	Amplitudes,
	FfsimStates,
	IdealDraw,
	Ints,
	KnownOutcomes,
	RotationPrograms,
	born_probabilities,
	on_active_qubits,
	remember,
)
from .pauli_frames import Bits, Floats, Program

logger = logging.getLogger(__name__)

PROGRAM_CACHE_SIZE = 16
STATE_CACHE_SIZE = 2

_H = np.array([[1, 1], [1, -1]], dtype=complex) / np.sqrt(2)
_SDG = np.array([[1, 0], [0, -1j]], dtype=complex)


def _apply_local(vector: Amplitudes, num_qubits: int, unitaries: dict[int, Amplitudes]) -> Amplitudes:
	"""Apply single-qubit unitaries to a little-endian statevector."""

	psi = np.asarray(vector)
	for q, m in sorted(unitaries.items()):
		# measured: a broadcast matmul instead took 11 s per basis at 24 qubits
		view = psi.reshape(1 << (num_qubits - 1 - q), 2, 1 << q)
		low, high = view[:, 0, :], view[:, 1, :]
		psi = np.stack([m[0, 0] * low + m[0, 1] * high, m[1, 0] * low + m[1, 1] * high], axis=1).reshape(-1)
	return psi


@dataclass(frozen=True)
class PreparedState:
	"""A circuit's statevector on its active qubits, with the active qubit each physical qubit is (``index``) and the
	physical qubit each measured logical qubit is (``physical``)."""

	amplitudes: Amplitudes
	index: dict[int, int]
	physical: list[int]

	def probabilities(self, basis: str) -> Floats:
		"""Outcome probabilities after rotating the measured qubits into ``basis``."""

		n = len(basis)
		rotation: dict[int, Amplitudes] = {}
		for position, letter in enumerate(basis):
			q = self.index.get(self.physical[n - 1 - position])
			if q is None:
				continue
			if letter == "X":
				rotation[q] = _H
			elif letter == "Y":
				rotation[q] = _H @ _SDG
		return born_probabilities(_apply_local(self.amplitudes, len(self.index), rotation) if rotation
								   else self.amplitudes)

	def outcomes(self, picks: Ints, logical: int, letter: str, rng: np.random.Generator) -> Bits:
		"""The sampled bit of one measured qubit in every shot."""

		p = self.physical[logical]
		if p in self.index:
			return ((picks >> self.index[p]) & 1).astype(bool)
		if letter in ("X", "Y"):
			return rng.random(len(picks)) < 0.5
		return np.zeros(len(picks), dtype=bool)


@dataclass
class ProxySampler:
	"""Samples a device's circuits as their ideal outcomes with its calibrated noise, caching states and programs; Aer
	simulates them within ``max_memory_mb`` MiB."""

	backend: Any
	max_memory_mb: int
	idle_model: str = "explicit"
	_calibration: Calibration | None = None
	_states: OrderedDict[str, NDArray[Any]] = field(default_factory=OrderedDict)
	_ffsim: OrderedDict[str, Any] = field(default_factory=OrderedDict)
	_tables: OrderedDict[str, Program] = field(default_factory=OrderedDict)
	stats: dict[str, int] = field(default_factory=lambda: {"ffsim": 0, "statevector": 0, "mps": 0, "prefix_hits": 0})

	def __post_init__(self) -> None:
		self._aer = AerStatevectors(self._states, STATE_CACHE_SIZE, self.max_memory_mb, self.stats)
		rotations = RotationPrograms(self._aer, self._states, STATE_CACHE_SIZE, self.max_memory_mb, self.stats)
		self._ideal = KnownOutcomes(FfsimStates(rotations, self._ffsim, STATE_CACHE_SIZE, self.stats))

	def __str__(self) -> str:
		return (f"ProxySampler(backend={self.backend!r}, max_memory_mb={self.max_memory_mb}, "
				f"idle_model={self.idle_model!r}, stats={self.stats})")

	__repr__ = __str__

	@property
	def calibration(self) -> Calibration:
		if self._calibration is None:
			self._calibration = Calibration.from_backend(self.backend)
		return self._calibration

	def sample(self, circuit: Any, shots: int, rng: np.random.Generator, logical: Any = None,
			   ideal_rng: np.random.Generator | None = None,
			   twirl: Bits | None = None) -> tuple[Bits, dict[str, Any]]:
		"""Bool matrix (shots x num_clbits) and diagnostics; ``ideal_rng`` draws ideal outcomes, ``rng`` the noise,
		``twirl`` applies measurement twirling."""

		draw = IdealDraw.of(circuit, logical, shots, ideal_rng if ideal_rng is not None else rng)
		ideal, method = self._ideal.outcomes(draw)
		info: dict[str, Any] = {"tier": "proxy", "ideal": method}
		budget = error_budget(circuit, self.calibration, self.idle_model)
		info.update(budget)
		out = self._apply_events(circuit, ideal, rng)
		info["events_per_shot"] = budget["events"]
		for qubit, clbit in draw.measured:
			out[:, clbit] = self.calibration.read_out(out[:, clbit], qubit, rng,
													  None if twirl is None else twirl[:, clbit])
		return out, info

	def sample_bases(
			self, isa: Any, physical: list[int], bases: list[str], shots_list: list[int],
			rng: np.random.Generator, measure_masks: Sequence[Bits] | None = None
	) -> list[tuple[Bits, dict[str, Any]]]:
		"""One record per Pauli basis from one prepared state and one set of noise frames; ``measure_masks``
		applies measurement twirling."""

		logger.debug("sample_bases: bases=%d qubits=%d", len(bases), len(bases[0]))
		n = len(bases[0])
		base = isa.remove_final_measurements(inplace=False)
		z_circuit = base.copy()
		register = ClassicalRegister(n, "m")
		z_circuit.add_register(register)
		for logical in range(n):
			z_circuit.measure(physical[logical], register[logical])
		compact, active = on_active_qubits(base)
		index = {q: k for k, q in enumerate(active)}
		state = PreparedState(self.prepared_state(compact), index, physical)
		program = self._program(z_circuit)
		flips_x, flips_z = program.propagate_frames(int(sum(shots_list)), rng)
		budget = error_budget(z_circuit, self.calibration, self.idle_model)
		out: list[tuple[Bits, dict[str, Any]]] = []
		offset = 0
		for b, (basis, shots) in enumerate(zip(bases, shots_list)):
			twirl = None if measure_masks is None else np.asarray(measure_masks[b], dtype=bool)
			frames = (flips_x[offset: offset + shots], flips_z[offset: offset + shots])
			offset += shots
			bits = self._basis_bits(state, basis, shots, rng, frames, twirl)
			out.append((bits, {"tier": "proxy", "ideal": "statevector-bases", **budget}))
		return out

	def prepared_state(self, compact: Any) -> Amplitudes:
		"""The whole circuit's statevector from Aer, cached per circuit; AerMemoryRefused when it does not fit."""

		return self._aer.statevector(compact)

	def _basis_bits(self, state: PreparedState, basis: str, shots: int, rng: np.random.Generator,
					frames: tuple[Bits, Bits], twirl: Bits | None) -> Bits:
		"""One basis's shots: the state rotated into it and sampled, each outcome flipped by its Pauli frame (and
		twirl, when set) and its readout error; idle qubits stay |0>."""

		n = len(basis)
		fx, fz = frames
		probabilities = state.probabilities(basis)
		picks = rng.choice(len(probabilities), size=shots, p=probabilities)
		bits = np.zeros((shots, n), dtype=bool)
		for position, letter in enumerate(basis):
			logical = n - 1 - position
			column = state.outcomes(picks, logical, letter, rng)
			if letter == "X":
				column ^= fz[:, logical]
			elif letter == "Y":
				column ^= fx[:, logical] ^ fz[:, logical]
			else:
				column ^= fx[:, logical]
			bits[:, logical] = self.calibration.read_out(column, state.physical[logical], rng,
														 None if twirl is None else twirl[:, logical])
		return bits

	def _program(self, circuit: Any) -> Program:
		key = circuit_digest(circuit)
		program = self._tables.get(key)
		if program is None:
			program = Program.from_circuit(circuit, self.calibration, self.idle_model)
			remember(self._tables, key, program, PROGRAM_CACHE_SIZE)
		return program

	def _apply_events(self, circuit: Any, ideal: Bits, rng: np.random.Generator) -> Bits:
		"""Ideal samples XOR the outcome flips of calibrated Pauli errors propagated as frames."""

		program = self._program(circuit)
		return ideal ^ program.propagate(ideal.shape[0], rng)
