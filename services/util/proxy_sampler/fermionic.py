from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
from qiskit import QuantumCircuit
from qiskit.circuit import Gate

SPINLESS = "spinless"
STATE_PREPARATION = frozenset({"x"})
IGNORED = frozenset({"barrier", "id", "global_phase"})
HOPPING = "xx_plus_yy"
PHASE_TURNS = {"z": 1.0, "s": 0.5, "sdg": -0.5, "t": 0.25, "tdg": -0.25}
ONE_QUBIT_PHASES = frozenset({"p", "rz", *PHASE_TURNS})
TWO_QUBIT_PHASES = frozenset({"cp", "cz", "rzz"})
ONE_BODY = frozenset({HOPPING, *ONE_QUBIT_PHASES})
DIAGONAL = frozenset({*ONE_QUBIT_PHASES, *TWO_QUBIT_PHASES})

Located = tuple[Any, tuple[int, ...]]


class NotFermionic(ValueError):
	pass


def ffsim_module() -> Any:
	try:
		import ffsim
	except ImportError:
		return None
	return ffsim


@dataclass(frozen=True)
class FermionSpace:
	norb: int
	nelec: int | tuple[int, int]

	@property
	def spinless(self) -> bool:
		return isinstance(self.nelec, int)

	def sector(self, qubit: int) -> int:
		return 0 if self.spinless else qubit // self.norb

	def one_particle(self, qubit: int) -> int | tuple[int, int]:
		if self.spinless:
			return 1
		return (1, 0) if qubit < self.norb else (0, 1)


@dataclass(frozen=True)
class FfsimProgram:
	circuit: Any
	space: FermionSpace


def _is_composite(operation: Any) -> bool:
	return type(operation) is Gate and operation.definition is not None


def _expanded(circuit: Any, qubits: tuple[int, ...]) -> Iterator[Located]:
	for instruction in circuit.data:
		where = tuple(qubits[circuit.find_bit(q).index] for q in instruction.qubits)
		yield instruction.operation, where


def _flat(operation: Any, qubits: tuple[int, ...]) -> list[Located]:
	if not _is_composite(operation):
		return [(operation, qubits)]
	return [leaf for inner, where in _expanded(operation.definition, qubits) for leaf in _flat(inner, where)]


def _names(gates: list[Located]) -> set[str]:
	return {operation.name for operation, _ in gates} - IGNORED


def _is_orbital_rotation(gates: list[Located], space: FermionSpace) -> bool:
	if not _names(gates) <= ONE_BODY:
		return False
	hops = [qubits for operation, qubits in gates if operation.name == HOPPING]
	return all(abs(a - b) == 1 and space.sector(a) == space.sector(b) for a, b in hops)


def _top_level(logical: Any) -> list[Located]:
	return list(_expanded(logical, tuple(range(logical.num_qubits))))


def _occupied(logical: Any) -> list[int]:
	occupied: list[int] = []
	touched: set[int] = set()
	for operation, qubits in (leaf for top in _top_level(logical) for leaf in _flat(*top)):
		if operation.name in IGNORED:
			continue
		if operation.name in STATE_PREPARATION and not touched & set(qubits):
			occupied.extend(qubits)
		touched.update(qubits)
	if not occupied:
		raise NotFermionic("it does not begin by occupying orbitals with x gates")
	return occupied


def fermion_space(logical: Any) -> FermionSpace:
	occupied = _occupied(logical)
	names = {operation.name for operation, _ in _top_level(logical)}
	if any(SPINLESS in name for name in names):
		return FermionSpace(logical.num_qubits, len(occupied))
	if logical.num_qubits % 2:
		raise NotFermionic(f"a spinful circuit has an even number of qubits, not {logical.num_qubits}")
	norb = logical.num_qubits // 2
	alpha = sum(q < norb for q in occupied)
	return FermionSpace(norb, (alpha, len(occupied) - alpha))


def _prepared_space(ffsim: Any, first: Any) -> FermionSpace | None:
	if isinstance(first, (ffsim.qiskit.PrepareHartreeFockJW, ffsim.qiskit.PrepareHartreeFockSpinlessJW)):
		return FermionSpace(first.norb, first.nelec)
	if isinstance(first, ffsim.qiskit.PrepareSlaterDeterminantJW):
		occupied_alpha, occupied_beta = first.occupied_orbitals
		return FermionSpace(first.norb, (len(occupied_alpha), len(occupied_beta)))
	if isinstance(first, ffsim.qiskit.PrepareSlaterDeterminantSpinlessJW):
		return FermionSpace(first.norb, len(first.occupied_orbitals))
	return None


def _orbital_rotation(ffsim: Any, gates: list[Located], space: FermionSpace) -> Any:
	width = space.norb if space.spinless else 2 * space.norb
	columns = []
	for orbital in range(width):
		circuit = QuantumCircuit(width)
		circuit.x(orbital)
		for operation, qubits in gates:
			circuit.append(operation, qubits)
		columns.append(ffsim.qiskit.final_state_vector(
			circuit, norb=space.norb, nelec=space.one_particle(orbital)).vec)
	if space.spinless:
		return ffsim.qiskit.OrbitalRotationSpinlessJW(space.norb, np.column_stack(columns))
	alpha, beta = np.column_stack(columns[:space.norb]), np.column_stack(columns[space.norb:])
	return ffsim.qiskit.OrbitalRotationJW(space.norb, (alpha, beta))


def _pair_phases(operation: Any) -> tuple[float, float, float]:
	if operation.name == "cz":
		return 0.0, 0.0, np.pi
	angle = float(operation.params[0])
	if operation.name == "cp":
		return 0.0, 0.0, angle
	return angle, angle, -2 * angle


def _single_phase(operation: Any) -> float:
	if operation.name in PHASE_TURNS:
		return PHASE_TURNS[operation.name] * np.pi
	return float(operation.params[0])


def _diagonal(ffsim: Any, gates: list[Located], space: FermionSpace) -> Any:
	width = space.norb if space.spinless else 2 * space.norb
	pair_phase = np.zeros((width, width))
	for operation, qubits in gates:
		if operation.name in IGNORED:
			continue
		if len(qubits) == 1:
			pair_phase[qubits[0], qubits[0]] += 2 * _single_phase(operation)
			continue
		a, b = qubits
		first, second, both = _pair_phases(operation)
		pair_phase[a, a] += 2 * first
		pair_phase[b, b] += 2 * second
		pair_phase[a, b] += both
		pair_phase[b, a] += both
	coulomb = -pair_phase
	if space.spinless:
		return ffsim.qiskit.DiagCoulombEvolutionSpinlessJW(space.norb, coulomb, 1.0)
	n = space.norb
	return ffsim.qiskit.DiagCoulombEvolutionJW(n, (coulomb[:n, :n], coulomb[:n, n:], coulomb[n:, n:]), 1.0)


def _blocks(operation: Any, qubits: tuple[int, ...], space: FermionSpace) -> Iterator[Located | list[Located]]:
	if not _is_composite(operation):
		yield operation, qubits
		return
	gates = _flat(operation, qubits)
	if _names(gates) <= STATE_PREPARATION:
		yield from gates
	elif _is_orbital_rotation(gates, space) or _names(gates) <= DIAGONAL:
		yield gates
	else:
		for inner, where in _expanded(operation.definition, qubits):
			yield from _blocks(inner, where, space)


def ffsim_program(logical: Any) -> FfsimProgram:
	ffsim = ffsim_module()
	logical = logical.remove_final_measurements(inplace=False)
	prepared = _prepared_space(ffsim, logical.data[0].operation) if logical.data else None
	if prepared is not None:
		return FfsimProgram(logical, prepared)
	space = fermion_space(logical)
	width = logical.num_qubits
	program = QuantumCircuit(width)
	for operation, qubits in _top_level(logical):
		for block in _blocks(operation, qubits, space):
			if isinstance(block, tuple):
				program.append(*block)
			elif _is_orbital_rotation(block, space):
				program.append(_orbital_rotation(ffsim, block, space), range(width))
			else:
				program.append(_diagonal(ffsim, block, space), range(width))
	return FfsimProgram(program, space)
