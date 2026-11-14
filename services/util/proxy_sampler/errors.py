class AerMemoryRefused(MemoryError):
	def __init__(self, computation, max_memory_mb, outcome):
		super().__init__(f"{computation} reached its {max_memory_mb} MiB memory limit: {outcome}")


class FfsimMemoryRefused(MemoryError):
	def __init__(self, need, max_memory_mb):
		super().__init__(f"{need}, over its {max_memory_mb} MiB memory limit")
