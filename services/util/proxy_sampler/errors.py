class AerMemoryRefused(MemoryError):
	def __init__(self, computation, max_memory_mb, outcome):
		super().__init__(f"{computation} reached its {max_memory_mb} MiB memory limit: {outcome}")
