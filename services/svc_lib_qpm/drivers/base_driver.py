# Base class for shim drivers. A driver adapts one lower-level library
# (QRMI, QDMI, …) to the QFw front-end contract and declares which contract
# calls it implements (its capability set). Unimplemented calls are NULL-ed
# out — the Frontend never routes a call to a driver that does not implement it.

class BaseDriver:
	# Subclasses set `name` (the library key used for routing/preference) and
	# `CAPABILITIES` (the subset of contract calls they cover).
	name = "base"
	CAPABILITIES = frozenset()

	def implements(self, call):
		return call in self.CAPABILITIES
