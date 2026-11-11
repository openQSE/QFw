"""A qubit's T1/T2 as the idle error it implies, and the maximum-noise stand-in for coherence a device does not
publish."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np
from numpy.typing import NDArray

WeightedPaulis = tuple[float, NDArray[np.float64]]
MAXIMUM_IDLE_ERROR = 0.75


def physical_t2(t1: float | None, t2: float) -> float:
	"""A published T2 limited to 2 T1 when T1 is published too."""

	return t2 if t1 is None else min(t2, 2 * t1)


def _survival(duration: float, time: float) -> float:
	return np.exp(-duration / time) if time > 0 else 0.0


@dataclass(frozen=True)
class Coherence:
	"""A qubit's published T1 and T2 in seconds."""

	t1: float
	t2: float
	known: ClassVar[bool] = True

	@classmethod
	def of(cls, t1: float | None, t2: float | None) -> Coherence | UnknownCoherence:
		"""The coherence of a qubit whose ``None`` times are unpublished: T2 <= 2 T1 holds, an unpublished T1 is
		the smallest it allows (T2 / 2), an unpublished T2 is fully dephasing (0), neither is unknown."""

		if t2 is None:
			return UnknownCoherence() if t1 is None else cls(float(t1), 0.0)
		if t1 is None:
			return cls(float(t2) / 2, float(t2))
		return cls(float(t1), float(physical_t2(t1, t2)))

	@classmethod
	def of_properties(cls, properties: Any) -> Coherence | UnknownCoherence:
		"""The coherence a backend reports for a qubit; none reported at all is unknown."""

		return cls.of(getattr(properties, "t1", None), getattr(properties, "t2", None))

	@property
	def effective_t2(self) -> float:
		"""The dephasing time that idling decays with, in seconds."""

		return self.t2

	def idle_error(self, duration: float) -> tuple[float, float]:
		"""(error events, dephasing events) of idling ``duration`` seconds."""

		if duration <= 0:
			return 0.0, 0.0
		relaxation = _survival(duration, self.t1)
		dephasing = _survival(duration, self.t2)
		return 1.5 * (0.5 - relaxation / 6.0 - dephasing / 3.0), 1.5 * max(0.0, (relaxation - dephasing) / 3.0)

	def idle_paulis(self, duration: float) -> WeightedPaulis | None:
		"""(probability, X/Y/Z distribution) of idling ``duration`` seconds; None for no idling."""

		if duration <= 0:
			return None
		p_xy = (1 - _survival(duration, self.t1)) / 4
		p_z = max(0.0, (1 - _survival(duration, self.t2)) / 2 - p_xy)
		probabilities = np.array([p_xy, p_xy, p_z])
		total = float(probabilities.sum())
		return (total, probabilities / total) if total > 0 else None


@dataclass(frozen=True)
class UnknownCoherence:
	"""Null Object for a qubit with no published T1 or T2: the T1, T2 -> 0 limit, maximum noise for any idling."""

	known: ClassVar[bool] = False
	effective_t2: ClassVar[float] = 0.0

	def idle_error(self, duration: float) -> tuple[float, float]:
		return (MAXIMUM_IDLE_ERROR, 0.0) if duration > 0 else (0.0, 0.0)

	def idle_paulis(self, duration: float) -> WeightedPaulis | None:
		return (MAXIMUM_IDLE_ERROR, np.full(3, 1 / 3)) if duration > 0 else None
