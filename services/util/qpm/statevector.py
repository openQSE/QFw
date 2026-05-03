class QFwStatevector:
	def __init__(self, amplitudes, num_qubits=None, source=None, metadata=None):
		self._amplitudes = [complex(amp) for amp in amplitudes]
		self._num_amplitudes = len(self._amplitudes)
		self._num_qubits = self._resolve_num_qubits(num_qubits)
		self._source = source
		self._metadata = metadata or {}

	def _resolve_num_qubits(self, num_qubits):
		if self._num_amplitudes < 1:
			raise ValueError("Statevector must contain at least one amplitude")

		if self._num_amplitudes & (self._num_amplitudes - 1):
			raise ValueError(
				"Statevector amplitude count must be a power of two")

		resolved = self._num_amplitudes.bit_length() - 1
		if num_qubits is not None and int(num_qubits) != resolved:
			raise ValueError(
				f"Statevector has {self._num_amplitudes} amplitudes, "
				f"which does not match num_qubits={num_qubits}")

		return resolved

	@classmethod
	def from_complex_sequence(cls, amplitudes, num_qubits=None, source=None,
							  metadata=None):
		return cls(amplitudes, num_qubits=num_qubits, source=source,
				   metadata=metadata)

	@classmethod
	def from_real_imag_pairs(cls, pairs, num_qubits=None, source=None,
							 metadata=None):
		amplitudes = [complex(real, imag) for real, imag in pairs]
		return cls(amplitudes, num_qubits=num_qubits, source=source,
				   metadata=metadata)

	def to_dict(self):
		payload = {
			"type": "statevector",
			"format": "complex128",
			"num_qubits": self._num_qubits,
			"num_amplitudes": self._num_amplitudes,
			"data": [
				[float(amplitude.real), float(amplitude.imag)]
				for amplitude in self._amplitudes
			],
		}

		if self._source:
			payload["source"] = self._source
		if self._metadata:
			payload["metadata"] = self._metadata

		return payload
