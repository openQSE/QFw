import hashlib
import pickle
from typing import Any

import numpy as np

from .gates import BARRIER_DELAY, GATES_DIGESTED_BY_PARAMETER_VALUE

OperationKey = tuple[str, tuple[str, ...], bytes]


def active_qubits(circuit: Any) -> list[int]:
	return sorted({circuit.find_bit(q).index for i in circuit.data if i.operation.name not in BARRIER_DELAY
		       for q in i.qubits})


def pauli_letter(x: bool, z: bool) -> str:
	return "Y" if x and z else "X" if x else "Z" if z else "I"


def _stable(value: Any) -> object:
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
	state = {k: v for k, v in vars(operation).items()
		 if k not in ("_definition", "_label", "_params", "_condition", "_duration", "_unit")}
	try:
		return pickle.dumps(_stable(state), protocol=4)
	except Exception:
		return repr(sorted(state.items(), key=lambda kv: kv[0])).encode()


def _definition_digest(operation: Any) -> bytes:
	definition = operation.definition
	return b"" if definition is None else circuit_digest(definition).encode()


def operation_key(operation: Any) -> OperationKey:
	params = tuple(_canonical_param(p) for p in operation.params)
	definition = b"" if operation.name in GATES_DIGESTED_BY_PARAMETER_VALUE else _definition_digest(operation)
	return operation.name, params, definition


def _canonical_param(value: Any) -> str:
	try:
		number = float(value)
	except (TypeError, ValueError):
		return str(value)
	return f"{0.0 if number == 0.0 else number:.12g}"


def circuit_digest(circuit: Any) -> str:
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
		elif operation.name not in GATES_DIGESTED_BY_PARAMETER_VALUE:
			digest.update(_operation_fingerprint(operation))
			digest.update(_definition_digest(operation))
	metadata = circuit.metadata or {}
	digest.update(repr(sorted((k, repr(v)) for k, v in metadata.items())).encode())
	return digest.hexdigest()
