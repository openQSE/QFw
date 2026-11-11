import hashlib
import pickle
from typing import Any

import numpy as np

from .gates import BARRIER_DELAY, STANDARD


def active_qubits(circuit: Any) -> list[int]:
	"""The qubits a circuit acts on outside delays and barriers, in increasing order."""

	return sorted({circuit.find_bit(q).index for i in circuit.data if i.operation.name not in BARRIER_DELAY
		       for q in i.qubits})


def pauli_letter(x: bool, z: bool) -> str:
	"""The Pauli acting on one qubit with symplectic bits ``x`` and ``z``."""

	return "Y" if x and z else "X" if x else "Z" if z else "I"


def _stable(value: Any) -> object:
	"""``value`` with numpy arrays replaced by (dtype, shape, C-order bytes), which pickle independently of layout."""

	if isinstance(value, np.ndarray):
		return ("ndarray", value.dtype.str, value.shape, np.ascontiguousarray(value).tobytes())
	if isinstance(value, (list, tuple)):
		return type(value).__name__, tuple(_stable(v) for v in value)
	if isinstance(value, dict):
		return "dict", tuple(sorted((repr(k), _stable(v)) for k, v in value.items()))
	if hasattr(value, "__dict__") and not isinstance(value, type):
		return type(value).__name__, _stable(dict(vars(value)))
	return value


def _operation_fingerprint(operation: Any) -> bytes:
	"""Stable bytes for a non-standard operation's attributes."""

	state = {k: v for k, v in vars(operation).items()
		 if k not in ("_definition", "_label", "_params", "_condition", "_duration", "_unit")}
	try:
		return pickle.dumps(_stable(state), protocol=4)
	except Exception:  # state that fails to pickle is digested by its repr
		return repr(sorted(state.items(), key=lambda kv: kv[0])).encode()


def _canonical_param(value: Any) -> str:
	"""A gate parameter as 12 significant digits, -0.0 written as 0, else its string."""

	try:
		number = float(value)
	except (TypeError, ValueError):
		return str(value)
	return f"{0.0 if number == 0.0 else number:.12g}"


def circuit_digest(circuit: Any) -> str:
	"""Content digest of a circuit, independent of its name and object identity (unlike QPY)."""

	digest = hashlib.sha256()
	registers = tuple((r.name, r.size) for r in circuit.cregs)
	digest.update(repr((circuit.num_qubits, circuit.num_clbits, registers)).encode())
	index_q = {q: i for i, q in enumerate(circuit.qubits)}
	index_c = {c: i for i, c in enumerate(circuit.clbits)}
	for instruction in circuit.data:
		operation = instruction.operation
		token = (operation.name, tuple(index_q[q] for q in instruction.qubits),
			 tuple(index_c[c] for c in instruction.clbits), tuple(_canonical_param(p) for p in operation.params))
		digest.update(repr(token).encode())
		if operation.name == "delay":
			digest.update(repr((operation.duration, operation.unit)).encode())
		elif operation.name not in STANDARD:
			digest.update(_operation_fingerprint(operation))
	metadata = circuit.metadata or {}
	digest.update(repr(sorted((k, repr(v)) for k, v in metadata.items())).encode())
	return digest.hexdigest()
