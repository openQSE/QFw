# What QRMI and QDMI profiles have in common. A driver adapts one library; a
# profile holds what differs per provider, under the same hook names where
# the two libraries mean the same thing.


class ProviderProfile:
	# The descriptor provider this profile serves.
	provider = None

	def __init__(self, descriptor=None):
		self._descriptor = dict(descriptor or {})

	def access(self, credential=None):
		"""The login this resource's library opens with (a dict).

		credential is the reservation's bound provider credential, the
		circuit's provider_credential, or None for a call made outside a
		reservation."""
		raise NotImplementedError

	def technology(self):
		# The qhw-device-v1 technology family, or None to leave it unset.
		return None
