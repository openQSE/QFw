"""Pauli-frame noise propagation for the calibrated proxy: a noisy shot is the ideal sample XOR its frame flips."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from qiskit.quantum_info import Clifford

from .calibration import PROCESS_PER_AVERAGE, Calibration, delay_seconds
from .circuits import active_qubits
from .coherence import WeightedPaulis
from .gates import CLIFFORD_1Q, NOISELESS

logger = logging.getLogger(__name__)

Bits = NDArray[np.bool_]
Floats = NDArray[np.float64]
FrameOp = tuple[str, tuple[int, ...], tuple[int, ...], float | None, Bits | None]


class _NoiseLocations:
	"""Where Pauli errors can occur: each location's weight (total probability), op, rows and Pauli distribution."""

	def __init__(self) -> None:
		self.weights: list[float] = []
		self.ops: list[int] = []
		self.rows: list[tuple[int, ...]] = []
		self.paulis: list[Floats] = []

	def add(self, position: int, rows: tuple[int, ...], weighted: WeightedPaulis | None) -> None:
		if weighted is None:
			return
		weight, paulis = weighted
		self.weights.append(weight)
		self.ops.append(position)
		self.rows.append(rows)
		self.paulis.append(paulis)

	def arrays(self) -> tuple[Floats, NDArray[np.int_], list[tuple[int, ...]], list[Floats]]:
		return np.asarray(self.weights, dtype=float), np.asarray(self.ops, dtype=int), self.rows, self.paulis


def _operation_entry(name: str, operation: Any, rows: tuple[int, ...], clbits: tuple[int, ...],
					 clifford_cache: dict[str, Bits | None]) -> FrameOp:
	"""(name, rows, clbits, rotation angle, Clifford table): the angle of a twirl-updated rotation, the table of a
	one- or two-qubit gate outside the hand-written updates (cached per gate name)."""

	param: float | None = None
	clifford: Bits | None = None
	if name in _TWIRL_UPDATES:
		try:
			param = float(operation.params[0])
		except Exception:  # noqa: BLE001 - a parameter that is not a number is kept as None
			param = None
	elif name not in CLIFFORD_1Q and name not in ("measure", "reset", "delay", "cz", "cx", "swap"):
		if name not in clifford_cache:
			clifford_cache[name] = _clifford_rows(operation) if operation.num_qubits <= 2 else None
		clifford = clifford_cache[name]
	return name, rows, clbits, param, clifford


_UNIFORM_PAULIS = {1: np.full(3, 1 / 3), 2: np.full(15, 1 / 15)}


def _gate_paulis(name: str, physical: tuple[int, ...], calibration: Calibration) -> WeightedPaulis | None:
	"""(probability, uniform Pauli distribution) of a one- or two-qubit gate's calibrated error; None without one."""

	error = calibration.gate_error(name, physical)
	k = len(physical)
	if error <= 0 or k > 2:
		return None
	return PROCESS_PER_AVERAGE[k] * error, _UNIFORM_PAULIS[k]


@dataclass
class Program:
	"""Pauli-frame simulation of circuit noise on qubits and clbits: Frame-propagation program of one ISA circuit."""

	ops: list[FrameOp]
	rows: int
	width: int
	weights: Floats
	location_op: NDArray[np.int_]
	location_rows: list[tuple[int, ...]]
	location_paulis: list[Floats]

	def __str__(self) -> str:
		return (f"Program({len(self.ops)} ops, rows={self.rows}, width={self.width}, "
				f"{len(self.weights)} error locations, events={self.events})")

	__repr__ = __str__

	@property
	def events(self) -> float:
		return float(self.weights.sum())

	@classmethod
	def from_circuit(cls, circuit: Any, calibration: Calibration, idle_model: str = "explicit") -> Program:
		logger.debug("Program.from_circuit: qubits=%d", circuit.num_qubits)
		active = active_qubits(circuit)
		index = {q: k for k, q in enumerate(active)}
		ops: list[FrameOp] = []
		noise = _NoiseLocations()
		clifford_cache: dict[str, Bits | None] = {}
		waits: dict[int, list[tuple[int, float]]] = {}
		for position, qubit, seconds in calibration.implicit_idle(circuit, idle_model):
			waits.setdefault(position, []).append((qubit, seconds))
		for position, instruction in enumerate(circuit.data):
			operation = instruction.operation
			name = operation.name
			physical = tuple(circuit.find_bit(q).index for q in instruction.qubits)
			if name == "barrier" or any(q not in index for q in physical):
				continue
			rows = tuple(index[q] for q in physical)
			clbits = tuple(circuit.find_bit(c).index for c in instruction.clbits)
			for qubit, seconds in waits.get(position, ()):
				noise.add(len(ops) - 1, (index[qubit],), calibration.coherence(qubit).idle_paulis(seconds))
			ops.append(_operation_entry(name, operation, rows, clbits, clifford_cache))
			if name == "delay":
				idle = calibration.coherence(physical[0]).idle_paulis(delay_seconds(operation, calibration.dt))
				noise.add(len(ops) - 1, rows, idle)
			elif name not in NOISELESS:
				noise.add(len(ops) - 1, rows, _gate_paulis(name, physical, calibration))
		return cls(ops, len(active), circuit.num_clbits, *noise.arrays())

	def propagate(self, shots: int, rng: np.random.Generator) -> Bits:
		"""Outcome flips (shots x clbits)."""

		return self._propagate(shots, rng, None)

	def propagate_frames(self, shots: int, rng: np.random.Generator) -> tuple[Bits, Bits]:
		"""The (x, z) frame bits (shots x clbits each) at each measurement."""

		frame_z = np.zeros((shots, self.width), dtype=bool)
		return self._propagate(shots, rng, frame_z), frame_z

	def _propagate(self, shots: int, rng: np.random.Generator, frame_z: Bits | None) -> Bits:
		"""Outcome flips (the x frame bits) at each measurement, the z frame bits written into ``frame_z`` if set."""

		flips = np.zeros((shots, self.width), dtype=bool)
		total = self.events
		if total <= 0 or not len(self.weights):
			return flips
		counts = rng.poisson(total, size=shots)
		n_events = int(counts.sum())
		if n_events == 0:
			return flips
		shot_of_event = np.repeat(np.arange(shots), counts)
		location = rng.choice(len(self.weights), size=n_events, p=self.weights / total)
		order = np.argsort(self.location_op[location], kind="stable")
		shot_of_event, location = shot_of_event[order], location[order]
		op_of_event = self.location_op[location]
		boundaries = np.searchsorted(op_of_event, np.arange(len(self.ops) + 1))

		x = np.zeros((self.rows, shots), dtype=bool)
		z = np.zeros((self.rows, shots), dtype=bool)
		live = np.zeros(self.rows, dtype=bool)
		for position, (name, rows, clbits, param, clifford) in enumerate(self.ops):
			if name == "measure":
				if live[rows[0]]:
					flips[:, clbits[0]] = x[rows[0]]
					if frame_z is not None:
						frame_z[:, clbits[0]] = z[rows[0]]
			elif any(live[r] for r in rows):
				_conjugate(name, rows, param, clifford, x, z, rng)
			start, stop = boundaries[position], boundaries[position + 1]
			if stop > start:
				self._inject(location[start:stop], shot_of_event[start:stop], x, z, live, rng)
		return flips

	def _inject(self, locations: NDArray[np.int_], shots_hit: NDArray[np.int_], x: Bits, z: Bits, live: Bits,
				rng: np.random.Generator) -> None:
		for loc in np.unique(locations):
			mask = locations == loc
			hit = shots_hit[mask]
			rows = self.location_rows[loc]
			probabilities = self.location_paulis[loc]
			codes = rng.choice(len(probabilities), size=len(hit), p=probabilities) + 1
			if len(rows) == 1:
				bits = _PAULI_XZ[codes]
				np.bitwise_xor.at(x[rows[0]], hit, bits[:, 0])
				np.bitwise_xor.at(z[rows[0]], hit, bits[:, 1])
			else:
				first, second = _PAULI_XZ[codes // 4], _PAULI_XZ[codes % 4]
				np.bitwise_xor.at(x[rows[0]], hit, first[:, 0])
				np.bitwise_xor.at(z[rows[0]], hit, first[:, 1])
				np.bitwise_xor.at(x[rows[1]], hit, second[:, 0])
				np.bitwise_xor.at(z[rows[1]], hit, second[:, 1])
			for r in rows:
				live[r] = True


def _clifford_rows(operation: Any) -> Bits | None:
	try:
		return np.asarray(Clifford(operation).symplectic_matrix, dtype=bool)
	except Exception:  # noqa: BLE001 - a non-Clifford operation has no symplectic rows
		return None


_PAULI_XZ = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=bool)


def _conjugate(name: str, rows: tuple[int, ...], param: float | None, clifford: Bits | None, x: Bits, z: Bits,
			   rng: np.random.Generator) -> None:
	"""Carry the frames (x, z) through one gate: Cliffords exactly, rotations twirled, anything else depolarizes."""

	if name in _FRAME_IDENTITY:
		return
	update = CLIFFORD_UPDATES.get(name)
	if update is not None:
		update(rows, x, z)
	elif name in _TWIRL_UPDATES and param is not None:
		_TWIRL_UPDATES[name](rows, x, z, float(np.sin(param) ** 2), rng)
	elif clifford is not None:
		_apply_clifford(rows, clifford, x, z)
	else:
		_depolarize(rows, x, z, rng)


def _sx(rows: tuple[int, ...], x: Bits, z: Bits) -> None:
	x[rows[0]] ^= z[rows[0]]


def _s(rows: tuple[int, ...], x: Bits, z: Bits) -> None:
	z[rows[0]] ^= x[rows[0]]


def _h(rows: tuple[int, ...], x: Bits, z: Bits) -> None:
	q = rows[0]
	x[q], z[q] = z[q].copy(), x[q].copy()


def _cz(rows: tuple[int, ...], x: Bits, z: Bits) -> None:
	a, b = rows
	z[a] ^= x[b]
	z[b] ^= x[a]


def _cx(rows: tuple[int, ...], x: Bits, z: Bits) -> None:
	c, t = rows
	x[t] ^= x[c]
	z[c] ^= z[t]


def _swap(rows: tuple[int, ...], x: Bits, z: Bits) -> None:
	a, b = rows
	x[[a, b]] = x[[b, a]]
	z[[a, b]] = z[[b, a]]


def _reset(rows: tuple[int, ...], x: Bits, z: Bits) -> None:
	x[rows[0]] = False
	z[rows[0]] = False


def _twirl_z(rows: tuple[int, ...], x: Bits, z: Bits, s2: float, rng: np.random.Generator) -> None:
	q = rows[0]
	anti = x[q]
	z[q] ^= anti & (rng.random(anti.shape[0]) < s2)


def _twirl_x(rows: tuple[int, ...], x: Bits, z: Bits, s2: float, rng: np.random.Generator) -> None:
	q = rows[0]
	anti = z[q]
	x[q] ^= anti & (rng.random(anti.shape[0]) < s2)


def _twirl_y(rows: tuple[int, ...], x: Bits, z: Bits, s2: float, rng: np.random.Generator) -> None:
	q = rows[0]
	anti = x[q] ^ z[q]
	toggle = anti & (rng.random(anti.shape[0]) < s2)
	x[q] ^= toggle
	z[q] ^= toggle


def _twirl_zz(rows: tuple[int, ...], x: Bits, z: Bits, s2: float, rng: np.random.Generator) -> None:
	a, b = rows
	anti = x[a] ^ x[b]
	toggle = anti & (rng.random(anti.shape[0]) < s2)
	z[a] ^= toggle
	z[b] ^= toggle


def _apply_clifford(rows: tuple[int, ...], clifford: Bits, x: Bits, z: Bits) -> None:
	"""Any other Clifford, from its symplectic matrix."""

	k = len(rows)
	bits = [x[r].copy() for r in rows] + [z[r].copy() for r in rows]
	for column, r in enumerate(rows):
		new_x = np.zeros_like(bits[0])
		new_z = np.zeros_like(bits[0])
		for i in range(2 * k):
			if clifford[i, column]:
				new_x ^= bits[i]
			if clifford[i, k + column]:
				new_z ^= bits[i]
		x[r], z[r] = new_x, new_z


def _depolarize(rows: tuple[int, ...], x: Bits, z: Bits, rng: np.random.Generator) -> None:
	"""A non-Clifford, non-twirlable gate: randomize the frame where it is non-trivial on these qubits."""

	support = np.zeros(x.shape[1], dtype=bool)
	for r in rows:
		support |= x[r] | z[r]
	for r in rows:
		x[r] = np.where(support, rng.random(x.shape[1]) < 0.5, x[r])
		z[r] = np.where(support, rng.random(x.shape[1]) < 0.5, z[r])


_FRAME_IDENTITY = frozenset({"x", "y", "z", "id", "delay"})
CLIFFORD_UPDATES: dict[str, Callable[[tuple[int, ...], Bits, Bits], None]] = {
	"sx": _sx, "sxdg": _sx, "s": _s, "sdg": _s, "h": _h, "cz": _cz, "cx": _cx, "swap": _swap, "reset": _reset}
_TWIRL_UPDATES: dict[str, Callable[[tuple[int, ...], Bits, Bits, float, np.random.Generator], None]] = {
	"rz": _twirl_z, "p": _twirl_z, "u1": _twirl_z, "rx": _twirl_x, "ry": _twirl_y, "rzz": _twirl_zz}
