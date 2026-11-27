import cmath
import math
import types

from util.qpm.statevector import decode_statevector_payload
from svc_fake_iqm_qpm.svc_qrc import QRC, fake_statevector


def _amplitudes(decoded):
	return [complex(value) for value in decoded]


def test_fake_statevector_is_the_closed_form():
	amplitudes = fake_statevector(3)
	phi = (math.sqrt(5.0) - 1.0) / 2.0
	expected = [cmath.exp(2j * math.pi * ((k * phi) % 1.0)) / math.sqrt(8)
		    for k in range(8)]

	assert all(abs(complex(a) - e) < 1e-12
		   for a, e in zip(amplitudes, expected))
	assert abs(sum(abs(complex(a)) ** 2 for a in amplitudes) - 1.0) < 1e-12
	assert fake_statevector(3) is amplitudes


def _circuit(**info):
	values = {"num_qubits": 3, "num_shots": 100}
	values.update(info)
	return types.SimpleNamespace(info=values)


def test_fake_qpm_counts_are_unchanged_without_a_statevector():
	qrc = QRC(start=False)

	assert qrc._measurement(_circuit().info, "000", 100, False) == {
		"000": 100}
	assert qrc._measurement(_circuit().info, "000", 100, True) == {}


def test_fake_qpm_returns_a_statevector_when_asked():
	qrc = QRC(start=False)

	result = qrc._measurement(
		_circuit(return_statevector=True).info, "000", 100, False)

	assert result["counts"] == {"000": 100}
	payload = result["statevector"]
	assert payload["encoding"] == "raw"
	assert payload["num_qubits"] == 3
	assert payload["source"] == "fake-iqm"
	decoded = _amplitudes(decode_statevector_payload(payload))
	assert all(abs(d - complex(e)) < 1e-12
		   for d, e in zip(decoded, fake_statevector(3)))
