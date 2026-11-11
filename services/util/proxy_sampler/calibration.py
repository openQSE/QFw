from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from qiskit.exceptions import QiskitError
from qiskit.utils.units import apply_prefix

from .circuits import active_qubits
from .coherence import Coherence, UnknownCoherence
from .gates import VIRTUAL

logger = logging.getLogger(__name__)

PROCESS_PER_AVERAGE = {1: 1.5, 2: 1.25}


def delay_seconds(operation: Any, dt: float | None) -> float:
	duration = float(operation.duration or 0)
	if operation.unit == "dt":
		return duration * dt if dt else 0.0
	return float(apply_prefix(duration, operation.unit))


@dataclass
class Calibration:
	_gate_error: dict[tuple[str, tuple[int, ...]], float]
	_gate_duration: dict[tuple[str, tuple[int, ...]], float]
	_coherence: dict[int, Coherence | UnknownCoherence]
	_readout: dict[int, tuple[float, float]]
	_dt: float | None

	def __str__(self) -> str:
		return (f"Calibration({len(self._gate_error)} gate errors, {len(self._coherence)} qubits' t1/t2, "
				f"{len(self._readout)} qubits' readout, dt={self._dt})")

	__repr__ = __str__

	def gate_error(self, name: str, qubits: tuple[int, ...]) -> float:
		return self._calibrated(self._gate_error, "error", name, qubits)

	def gate_duration(self, name: str, qubits: tuple[int, ...]) -> float:
		return self._calibrated(self._gate_duration, "duration", name, qubits)

	@staticmethod
	def _calibrated(values: dict[tuple[str, tuple[int, ...]], float], quantity: str, name: str,
					qubits: tuple[int, ...]) -> float:
		if (name, qubits) not in values:
			raise LookupError(f"the device reports no {quantity} for {name} on qubits {qubits}")
		return values[(name, qubits)]

	def coherence(self, qubit: int) -> Coherence | UnknownCoherence:
		return self._coherence.get(qubit, UnknownCoherence())

	def readout_flips(self, qubit: int) -> tuple[float, float]:
		if qubit not in self._readout:
			raise LookupError(f"the device reports no readout error for qubit {qubit}")
		return self._readout[qubit]

	def read_out(self, column: NDArray[np.bool_], qubit: int, rng: np.random.Generator,
				 twirl: NDArray[np.bool_] | None) -> NDArray[np.bool_]:
		flip_up, flip_down = self.readout_flips(qubit)
		measured = column if twirl is None else column ^ twirl
		draws = rng.random(len(column))
		read: NDArray[np.bool_] = measured ^ np.where(measured, draws < flip_down, draws < flip_up)
		return read if twirl is None else read ^ twirl

	@property
	def dt(self) -> float | None:
		return self._dt

	def implicit_idle(self, circuit: Any, idle_model: str) -> list[tuple[int, int, float]]:
		if idle_model != "scheduled":
			return []
		resolution = self._dt / 2 if self._dt else 0.0
		clock: dict[int, float] = {}
		free: dict[int, float] = {}
		started: set[int] = set()
		gaps: list[tuple[int, int, float]] = []
		for position, instruction in enumerate(circuit.data):
			operation = instruction.operation
			qubits = tuple(circuit.find_bit(q).index for q in instruction.qubits)
			if operation.name == "barrier":
				sync = max((clock.get(q, 0.0) for q in qubits), default=0.0)
				clock.update((q, sync) for q in qubits)
				continue
			start = max(clock.get(q, 0.0) for q in qubits)
			if operation.name == "delay":
				duration = delay_seconds(operation, self._dt)
			else:
				duration = self.gate_duration(operation.name, qubits)
				started.update(qubits)
			for q in qubits:
				gap = start - free.get(q, 0.0)
				if q in started and gap > resolution:
					gaps.append((position, q, gap))
				clock[q] = free[q] = start + duration
		return gaps

	@classmethod
	def from_backend(cls, backend: Any) -> Calibration:
		target = backend.target
		errors: dict[tuple[str, tuple[int, ...]], float] = {}
		durations: dict[tuple[str, tuple[int, ...]], float] = {}
		for name in target.operation_names:
			props = target[name]
			if props is None:
				continue
			for qargs, prop in props.items():
				if prop is None or qargs is None:
					continue
				if prop.error is not None:
					errors[(name, tuple(qargs))] = float(prop.error)
				if prop.duration is not None:
					durations[(name, tuple(qargs))] = float(prop.duration)
		coherence: dict[int, Coherence | UnknownCoherence] = {}
		for q in range(target.num_qubits):
			qp = target.qubit_properties[q] if target.qubit_properties else None
			coherence[q] = Coherence.of_properties(qp)
		properties = backend.properties() if hasattr(backend, "properties") else None
		readout: dict[int, tuple[float, float]] = {}
		for q in range(target.num_qubits):
			flips = _assignment_errors(properties, q)
			if flips is None and ("measure", (q,)) in errors:
				flips = (errors[("measure", (q,))],) * 2
			if flips is not None:
				readout[q] = flips
		return cls(errors, durations, coherence, readout, getattr(target, "dt", None))


def _assignment_errors(properties: Any, qubit: int) -> tuple[float, float] | None:
	if properties is None:
		return None
	try:
		p10 = properties.qubit_property(qubit, "prob_meas1_prep0")[0]
		p01 = properties.qubit_property(qubit, "prob_meas0_prep1")[0]
	except QiskitError:
		return None
	return None if p10 is None or p01 is None else (float(p10), float(p01))


def error_budget(circuit: Any, calibration: Calibration, idle_model: str = "explicit") -> dict[str, float | int]:
	logger.debug("error_budget: qubits=%d idle_model=%s", circuit.num_qubits, idle_model)
	budget = _ErrorBudget(calibration)
	for instruction in circuit.data:
		name = instruction.operation.name
		qubits = tuple(circuit.find_bit(q).index for q in instruction.qubits)
		if name == "barrier":
			continue
		if name == "delay":
			budget.delay(qubits[0], delay_seconds(instruction.operation, calibration.dt))
		else:
			budget.gate(name, qubits)
	gaps = calibration.implicit_idle(circuit, idle_model)
	for _, qubit, seconds in gaps:
		budget.idle_event(qubit, seconds)
	implicit = float(sum(seconds for _, _, seconds in gaps))
	return {"events": budget.lam, "z_events": budget.lam_z, "fidelity": float(np.exp(-budget.lam)),
			"gates_1q": budget.gates_1q, "gates_2q": budget.gates_2q, "delay_s": budget.delays,
			"implicit_idle_s": implicit, "log10_fidelity": float(-budget.lam / np.log(10)),
			"unknown_coherence_qubits": sum(not calibration.coherence(q).known for q in active_qubits(circuit))}


class _ErrorBudget:
	def __init__(self, calibration: Calibration) -> None:
		self.calibration = calibration
		self.lam = self.lam_z = self.delays = 0.0
		self.gates_1q = self.gates_2q = 0

	def idle_event(self, q: int, t: float) -> None:
		if t <= 0:
			return
		error, dephasing = self.calibration.coherence(q).idle_error(t)
		self.lam += error
		self.lam_z += min(dephasing, error)

	def delay(self, q: int, duration: float) -> None:
		self.delays += duration
		self.idle_event(q, duration)

	def gate(self, name: str, qubits: tuple[int, ...]) -> None:
		if name in VIRTUAL:
			return
		error = self.calibration.gate_error(name, qubits)
		self.lam += PROCESS_PER_AVERAGE[min(len(qubits), 2)] * error
		if len(qubits) == 1:
			self.lam_z += 0.5 * error
			self.gates_1q += 1
		else:
			self.lam_z += 0.25 * error
			self.gates_2q += 1
