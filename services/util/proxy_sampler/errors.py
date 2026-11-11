class AerMemoryRefused(MemoryError):
	def __init__(self, computation, max_memory_mb, outcome):
		super().__init__(f"{computation} reached its {max_memory_mb} MiB memory limit: {outcome}")


class FfsimMemoryRefused(MemoryError):
	def __init__(self, space, needed_mb, max_memory_mb):
		super().__init__(f"ffsim state vector of {space} needs {needed_mb} MiB, over its {max_memory_mb} MiB memory limit")
