from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np
from numpy.typing import NDArray

WeightedPaulis = tuple[float, NDArray[np.float64]]
MAXIMUM_IDLE_ERROR = 0.75


def physical_t2(t1: float | None, t2: float) -> float:
	return t2 if t1 is None else min(t2, 2 * t1)


def _survival(duration: float, time: float) -> float:
	return np.exp(-duration / time) if time > 0 else 0.0


@dataclass(frozen=True)
class Coherence:
	t1: float
	t2: float
	known: ClassVar[bool] = True

	@classmethod
	def of(cls, t1: float | None, t2: float | None) -> Coherence | UnknownCoherence:
		if t2 is None:
			return UnknownCoherence() if t1 is None else cls(float(t1), 0.0)
		if t1 is None:
			return cls(float(t2) / 2, float(t2))
		return cls(float(t1), float(physical_t2(t1, t2)))

	@classmethod
	def of_properties(cls, properties: Any) -> Coherence | UnknownCoherence:
		return cls.of(getattr(properties, "t1", None), getattr(properties, "t2", None))

	@property
	def effective_t2(self) -> float:
		return self.t2

	def idle_error(self, duration: float) -> tuple[float, float]:
		if duration <= 0:
			return 0.0, 0.0
		relaxation = _survival(duration, self.t1)
		dephasing = _survival(duration, self.t2)
		return 1.5 * (0.5 - relaxation / 6.0 - dephasing / 3.0), 1.5 * max(0.0, (relaxation - dephasing) / 3.0)

	def idle_paulis(self, duration: float) -> WeightedPaulis | None:
		if duration <= 0:
			return None
		p_xy = (1 - _survival(duration, self.t1)) / 4
		p_z = max(0.0, (1 - _survival(duration, self.t2)) / 2 - p_xy)
		probabilities = np.array([p_xy, p_xy, p_z])
		total = float(probabilities.sum())
		return (total, probabilities / total) if total > 0 else None


@dataclass(frozen=True)
class UnknownCoherence:
	known: ClassVar[bool] = False
	effective_t2: ClassVar[float] = 0.0

	def idle_error(self, duration: float) -> tuple[float, float]:
		return (MAXIMUM_IDLE_ERROR, 0.0) if duration > 0 else (0.0, 0.0)

	def idle_paulis(self, duration: float) -> WeightedPaulis | None:
		return (MAXIMUM_IDLE_ERROR, np.full(3, 1 / 3)) if duration > 0 else None
