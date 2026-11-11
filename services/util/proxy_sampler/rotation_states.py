"""Exact statevectors of circuits made of Pauli gates and Pauli exponentials, run in Aer as rotation programs."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray
from qiskit import QuantumCircuit, transpile
from qiskit.circuit.library import PauliEvolutionGate
from qiskit.quantum_info import SparsePauliOp
from qiskit_aer import AerSimulator

from .circuits import pauli_letter
from .errors import AerMemoryRefused

REAL_COEFFICIENT_TOL = 1e-12
MAX_DEFINITION_DEPTH = 12
# The status Aer reports for an experiment whose method needs more than max_memory_mb, before it allocates.
AER_MEMORY_REFUSAL = "Insufficient memory"

RotationProgram = list[tuple[str, list[int], float | None]]


class _Unsupported(Exception):
	"""A circuit operation that is neither a Pauli gate nor a real Pauli exponential."""


def _single_term(operation: Any) -> bool:
	operator = getattr(operation, "operator", None)
	return operator is not None and not isinstance(operator, list) and len(operator) == 1


_PAULI_GATES = {"x": "X", "y": "Y", "z": "Z"}


_ROTATIONS = {"rx": "X", "ry": "Y", "rz": "Z", "rxx": "XX", "ryy": "YY", "rzz": "ZZ"}


def _rotation_ops(circuit: Any, qubits: list[int] | None = None, depth: int = 0) -> RotationProgram:
	"""The circuit as (letters, qubits, angle) with U = exp(-i angle P) (angle None: the Pauli P itself)."""

	ops: RotationProgram = []
	for instruction in circuit.data:
		operation = instruction.operation
		name = operation.name
		where = [circuit.find_bit(q).index for q in instruction.qubits]
		if qubits is not None:
			where = [qubits[q] for q in where]
		if name in ("barrier", "measure", "id", "delay"):
			continue
		if name in _PAULI_GATES:
			ops.append((_PAULI_GATES[name], where, None))
		elif name in _ROTATIONS:
			ops.append((_ROTATIONS[name], where, float(operation.params[0]) / 2))
		elif name == "PauliEvolution" and _single_term(operation):
			op = operation.operator
			pauli = op.paulis[0]
			coeff = complex(op.coeffs[0]) * (-1j) ** int(pauli.phase)
			if abs(coeff.imag) > REAL_COEFFICIENT_TOL:
				raise _Unsupported("complex coefficient")
			letters = "".join(pauli_letter(x, z) for x, z in zip(pauli.x, pauli.z))
			ops.append((letters, where, float(operation.params[0]) * coeff.real))
		elif operation.definition is not None and depth < MAX_DEFINITION_DEPTH:
			ops.extend(_rotation_ops(operation.definition, where, depth + 1))
		else:
			raise _Unsupported(name)
	return ops


def rotation_program(circuit: Any) -> RotationProgram | None:
	try:
		return _rotation_ops(circuit.remove_final_measurements(inplace=False))
	except (_Unsupported, TypeError, ValueError):
		return None


def aer_result(circuit: Any, method: str, max_memory_mb: int, **run_options: Any) -> Any:
	"""Aer's result of ``circuit`` simulated by ``method`` within ``max_memory_mb`` MiB; AerMemoryRefused when Aer
	reports the method needs more before it allocates, RuntimeError on any other failure it reports."""

	result = AerSimulator(method=method, max_memory_mb=max_memory_mb).run(circuit, **run_options).result()
	if not result.success:
		status = str(result.results[0].status if result.results else result.status)
		if AER_MEMORY_REFUSAL in status:
			raise AerMemoryRefused(f"Aer {method} simulation", max_memory_mb, status)
		raise RuntimeError(f"Aer {method} simulation failed: {status}")
	return result


def aer_statevector(circuit: Any, max_memory_mb: int) -> NDArray[np.complex128]:
	"""The final statevector of ``circuit``, simulated by Aer within ``max_memory_mb`` MiB; AerMemoryRefused when Aer
	finds it does not fit. A circuit on no qubits, which Aer cannot save a state of, is its global phase."""

	if circuit.num_qubits == 0:
		return np.array([np.exp(1j * float(circuit.global_phase))], dtype=np.complex128)
	saved = circuit.copy()
	saved.save_statevector()
	return np.asarray(aer_result(saved, "statevector", max_memory_mb, shots=1).get_statevector(0))


def rotation_circuit(num_qubits: int, ops: RotationProgram) -> QuantumCircuit:
	"""The rotation program as Pauli gates and Pauli evolutions on ``num_qubits`` qubits, transpiled for Aer."""

	program = QuantumCircuit(num_qubits)
	for letters, where, angle in ops:
		pairs = [(letter, q) for letter, q in zip(letters, where) if letter != "I"]
		if not pairs:
			continue
		if angle is None:
			for letter, q in pairs:
				getattr(program, letter.lower())(q)
		else:
			label = "".join(letter for letter, _ in reversed(pairs))
			program.append(PauliEvolutionGate(SparsePauliOp(label), time=angle), [q for _, q in pairs])
	return transpile(program, AerSimulator(method="statevector"), optimization_level=0)
