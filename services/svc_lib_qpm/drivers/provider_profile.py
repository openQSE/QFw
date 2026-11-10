class ProviderProfile:
	provider = None

	def __init__(self, descriptor=None):
		self._descriptor = dict(descriptor or {})

	def access(self, credential=None):
		raise NotImplementedError

	def technology(self):
		return None
