import pytest

from util.qpm.statevector import (
	QFwStatevector,
	decode_statevector_payload,
	encode_statevector_payload,
)

AMPLITUDES = [complex(0.5, 0.5), complex(0.0, -0.5), complex(0.5, 0.0),
	      complex(-0.5, 0.0)]


def _amplitudes(decoded):
	return [complex(value) for value in decoded]


def test_a_statevector_is_raw_unless_text_is_asked_for():
	payload = encode_statevector_payload(AMPLITUDES, num_qubits=2)
	assert payload["encoding"] == "raw"
	assert isinstance(payload["data"], bytes)
	assert payload["raw_size_bytes"] == 64
	assert payload["num_amplitudes"] == 4
	assert "compressed_size_bytes" not in payload

	text = encode_statevector_payload(
		AMPLITUDES, num_qubits=2, encoding="base64+zlib")
	assert text["encoding"] == "base64+zlib"
	assert isinstance(text["data"], str)


@pytest.mark.parametrize("encoding", ["base64+zlib", "raw"])
def test_both_encodings_decode_to_the_same_amplitudes(encoding):
	payload = QFwStatevector(AMPLITUDES, num_qubits=2).to_dict(
		encoding=encoding)

	assert _amplitudes(decode_statevector_payload(payload)) == AMPLITUDES


@pytest.mark.parametrize("wrap", [bytes, bytearray, memoryview])
def test_raw_decodes_any_buffer(wrap):
	payload = encode_statevector_payload(
		AMPLITUDES, num_qubits=2, encoding="raw")
	payload["data"] = wrap(payload["data"])

	assert _amplitudes(decode_statevector_payload(payload)) == AMPLITUDES


def test_raw_decode_checks_what_the_payload_says():
	payload = encode_statevector_payload(
		AMPLITUDES, num_qubits=2, encoding="raw")
	short = dict(payload, data=payload["data"][:48])
	with pytest.raises(ValueError, match="raw size mismatch"):
		decode_statevector_payload(short)
	with pytest.raises(ValueError, match="must be bytes"):
		decode_statevector_payload(dict(payload, data="not bytes"))
	with pytest.raises(ValueError, match="Unsupported statevector encoding"):
		decode_statevector_payload(dict(payload, encoding="gzip"))
