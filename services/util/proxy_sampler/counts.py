import numpy as np

from .pauli_frames import Bits


def counts_of(bits: Bits) -> dict[str, int]:
	words, tally = np.unique(bits[:, ::-1], axis=0, return_counts=True)
	return {"".join("1" if bit else "0" for bit in word): int(count) for word, count in zip(words, tally)}
