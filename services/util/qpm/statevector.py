import base64
import struct
import time
import zlib


STATEVECTOR_ENCODING = "base64+zlib"
STATEVECTOR_DTYPE = "complex128"
STATEVECTOR_BYTE_ORDER = "little"
STATEVECTOR_BYTES_PER_AMPLITUDE = 16

try:
	import numpy as _np
except Exception:
	_np = None


def _numpy_available():
	return (
		_np is not None and
		hasattr(_np, "ascontiguousarray") and
		hasattr(_np, "dtype") and
		hasattr(_np, "frombuffer")
	)


def _complex128_dtype():
	return _np.dtype("<c16")


def _statevector_bytes(amplitudes):
	if _numpy_available():
		arr = _np.ascontiguousarray(amplitudes, dtype=_complex128_dtype())
		return arr.tobytes(), int(arr.size)

	raw = bytearray()
	count = 0
	for amplitude in amplitudes:
		value = complex(amplitude)
		raw.extend(struct.pack("<dd", value.real, value.imag))
		count += 1
	return bytes(raw), count


def _statevector_from_bytes(raw):
	if len(raw) % STATEVECTOR_BYTES_PER_AMPLITUDE:
		raise ValueError(
			"Statevector byte length is not aligned to complex128")

	if _numpy_available():
		return _np.frombuffer(raw, dtype=_complex128_dtype())

	return [
		complex(real, imag)
		for real, imag in struct.iter_unpack("<dd", raw)
	]


def _resolve_num_qubits(num_amplitudes, num_qubits=None):
	if num_amplitudes < 1:
		raise ValueError("Statevector must contain at least one amplitude")

	if num_amplitudes & (num_amplitudes - 1):
		raise ValueError(
			"Statevector amplitude count must be a power of two")

	resolved = num_amplitudes.bit_length() - 1
	if num_qubits is not None and int(num_qubits) != resolved:
		raise ValueError(
			f"Statevector has {num_amplitudes} amplitudes, "
			f"which does not match num_qubits={num_qubits}")

	return resolved


def _compression_ratio(raw_size, compressed_size):
	if raw_size <= 0:
		return 0.0
	return compressed_size / raw_size


def encode_statevector_payload(amplitudes, num_qubits=None, source=None,
			       metadata=None):
	raw, num_amplitudes = _statevector_bytes(amplitudes)
	return _encode_statevector_bytes(
		raw, num_amplitudes, num_qubits=num_qubits,
		source=source, metadata=metadata)


def _encode_statevector_bytes(raw, num_amplitudes, num_qubits=None,
			      source=None, metadata=None):
	start = time.time()
	resolved_qubits = _resolve_num_qubits(num_amplitudes, num_qubits)
	compressed = zlib.compress(raw)
	encoded = base64.b64encode(compressed).decode("ascii")
	payload = {
		"type": "statevector",
		"encoding": STATEVECTOR_ENCODING,
		"dtype": STATEVECTOR_DTYPE,
		"byte_order": STATEVECTOR_BYTE_ORDER,
		"num_qubits": resolved_qubits,
		"num_amplitudes": num_amplitudes,
		"raw_size_bytes": len(raw),
		"compressed_size_bytes": len(compressed),
		"base64_size_bytes": len(encoded),
		"compression_ratio": _compression_ratio(len(raw), len(compressed)),
		"encode_time_seconds": time.time() - start,
		"data": encoded,
	}

	if source:
		payload["source"] = source
	if metadata:
		payload["metadata"] = metadata

	return payload


def decode_statevector_payload(payload):
	if not isinstance(payload, dict):
		raise ValueError("Statevector payload must be a mapping")
	if payload.get("type") != "statevector":
		raise ValueError(f"Unsupported statevector payload: {payload}")
	if payload.get("encoding") != STATEVECTOR_ENCODING:
		raise ValueError(
			f"Unsupported statevector encoding: {payload.get('encoding')}")
	if payload.get("dtype") != STATEVECTOR_DTYPE:
		raise ValueError(
			f"Unsupported statevector dtype: {payload.get('dtype')}")
	if payload.get("byte_order") not in (None, STATEVECTOR_BYTE_ORDER):
		raise ValueError(
			f"Unsupported statevector byte order: "
			f"{payload.get('byte_order')}")

	encoded = payload.get("data")
	if not isinstance(encoded, str):
		raise ValueError("Statevector data must be a base64 string")

	if payload.get("base64_size_bytes") is not None:
		expected_base64_size = int(payload.get("base64_size_bytes"))
		if expected_base64_size != len(encoded):
			raise ValueError(
				f"Statevector base64 size mismatch: expected "
				f"{expected_base64_size}, got {len(encoded)}")

	compressed = base64.b64decode(encoded.encode("ascii"), validate=True)
	expected_compressed_size = payload.get("compressed_size_bytes")
	if (expected_compressed_size is not None and
			int(expected_compressed_size) != len(compressed)):
		raise ValueError(
			f"Statevector compressed size mismatch: expected "
			f"{expected_compressed_size}, got {len(compressed)}")

	raw = zlib.decompress(compressed)
	expected_raw_size = payload.get("raw_size_bytes")
	if expected_raw_size is not None and int(expected_raw_size) != len(raw):
		raise ValueError(
			f"Statevector raw size mismatch: expected "
			f"{expected_raw_size}, got {len(raw)}")

	num_amplitudes = len(raw) // STATEVECTOR_BYTES_PER_AMPLITUDE
	expected_amplitudes = payload.get("num_amplitudes")
	if (expected_amplitudes is not None and
			int(expected_amplitudes) != num_amplitudes):
		raise ValueError(
			f"Statevector amplitude count mismatch: expected "
			f"{expected_amplitudes}, got {num_amplitudes}")

	_resolve_num_qubits(num_amplitudes, payload.get("num_qubits"))
	return _statevector_from_bytes(raw)


def statevector_payload_size_summary(payload):
	return {
		"num_qubits": payload.get("num_qubits"),
		"num_amplitudes": payload.get("num_amplitudes"),
		"raw_size_bytes": payload.get("raw_size_bytes"),
		"compressed_size_bytes": payload.get("compressed_size_bytes"),
		"base64_size_bytes": payload.get("base64_size_bytes"),
		"compression_ratio": payload.get("compression_ratio"),
		"encode_time_seconds": payload.get("encode_time_seconds"),
	}


class QFwStatevector:
	def __init__(self, amplitudes, num_qubits=None, source=None, metadata=None):
		self._raw, self._num_amplitudes = _statevector_bytes(amplitudes)
		self._num_qubits = _resolve_num_qubits(
			self._num_amplitudes, num_qubits)
		self._source = source
		self._metadata = metadata or {}

	def _resolve_num_qubits(self, num_qubits):
		return _resolve_num_qubits(self._num_amplitudes, num_qubits)

	@classmethod
	def from_complex_sequence(cls, amplitudes, num_qubits=None, source=None,
							  metadata=None):
		return cls(amplitudes, num_qubits=num_qubits, source=source,
				   metadata=metadata)

	def to_dict(self):
		return _encode_statevector_bytes(
			self._raw, self._num_amplitudes,
			num_qubits=self._num_qubits, source=self._source,
			metadata=self._metadata)
