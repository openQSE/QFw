class AerMemoryRefused(MemoryError):
	"""Aer refused to simulate a circuit by a method that needs more than the memory it was given."""

	def __init__(self, computation, max_memory_mb, outcome):
		super().__init__(f"{computation} reached its {max_memory_mb} MiB memory limit: {outcome}")
