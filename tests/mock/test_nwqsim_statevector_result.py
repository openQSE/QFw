import types

import pytest

from defw_exception import DEFwError
from svc_nwqsim_qpm.svc_qrc import QRC


class _Statevector:
	def to_dict(self):
		from util.qpm.statevector import encode_statevector_payload

		return encode_statevector_payload(
			[complex(1.0, 0.0), complex(0.0, 0.0)],
			num_qubits=1, source="test")


def _circuit(dump_file, return_statevector=True):
	return types.SimpleNamespace(info={
		"return_statevector": return_statevector,
		"_qfw_statevector_dump": str(dump_file),
		"num_qubits": 1,
	})


def _qrc():
	return QRC(start=False)


def test_statevector_result_allows_output_without_measurements(
		monkeypatch, tmp_path):
	dump_file = tmp_path / "statevector.dump"
	dump_file.touch()
	qrc = _qrc()
	monkeypatch.setattr(
		qrc, "parse_statevector_dump", lambda path, qubits: _Statevector())

	result = qrc.parse_task_result(
		b"NWQSim statevector completed\n", _circuit(dump_file), {})

	statevector = result["statevector"]
	assert result["counts"] == {}
	assert statevector["type"] == "statevector"
	assert statevector["encoding"] == "base64+zlib"
	assert statevector["dtype"] == "complex128"
	assert statevector["num_qubits"] == 1
	assert statevector["num_amplitudes"] == 2
	assert isinstance(statevector["data"], str)
	assert len(statevector["data"]) == statevector["base64_size_bytes"]


def test_statevector_result_keeps_measurement_parsing_strict(tmp_path):
	dump_file = tmp_path / "statevector.dump"
	dump_file.touch()
	qrc = _qrc()
	out = b"===============  Measurement\ninvalid result\n"

	with pytest.raises(DEFwError):
		qrc.parse_task_result(out, _circuit(dump_file), {})


def test_count_result_still_requires_measurements(tmp_path):
	qrc = _qrc()

	with pytest.raises(DEFwError):
		qrc.parse_task_result(
			b"NWQSim completed without measurements\n",
			_circuit(tmp_path / "unused.dump", return_statevector=False),
			{},
		)
