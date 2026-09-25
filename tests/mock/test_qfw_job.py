import base64
import sys
import types

import pytest

from tests.mock.fakes import (
	FakeEventAPI,
	FakeQPM,
	FakeSlurmDriver,
	make_result_event,
)


class FakeBackend:
	COMPLETION_TIMEOUT_SEC = 5

	def __init__(self, statevector=False):
		self.logged_results = []
		self.dump_called = False
		self._statevector = statevector

	def returns_statevector(self):
		return self._statevector

	def log_statistics(self, result):
		self.logged_results.append(result)

	def dump_statistics(self):
		self.dump_called = True

	def my_name(self):
		return "Fake Backend"

	def my_version(self):
		return "test-version"


def _stub_qasm(monkeypatch, text="OPENQASM 2.0;"):
	# The circuit is serialized in util.circuit_payload now, which imports
	# qiskit when it is called rather than at module import, so the stub goes
	# on the qiskit module itself.
	import qiskit

	monkeypatch.setattr(qiskit.qasm2, "dumps", lambda circ: text)


def _driver_options(**options):
	return FakeSlurmDriver().execution_options(**options)


@pytest.mark.parametrize("value", ["0", "0x1", "-1", "1.0", True])
def test_qfw_job_rejects_noncanonical_reservation_ids(value):
	import qfw_qiskit.qfw_job as qfw_job

	with pytest.raises(qfw_job.DEFwError):
		qfw_job.normalize_reservation_id(value)


def test_qfw_job_submit_builds_expected_payload(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-101"])
	fake_event_api = FakeEventAPI()
	backend = FakeBackend()
	circuit = qfw_job.QuantumCircuit(3, name="payload-circuit")
	options = _driver_options(shots=17, seed=5, seed_simulator=11)

	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, fake_event_api, circuit, options)
	job.submit()

	assert len(fake_qpm.submitted_payloads) == 1
	assert fake_qpm.submitted_payloads[0] == {
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 3,
		"num_shots": 17,
		"compiler": "staq",
		"reservation_id": 1,
	}
	assert len(job._cid_list) == 1
	assert list(job._cid_list[0].keys()) == ["cid-101"]


@pytest.mark.skip(
	reason="Phase 3+ QFwJob result memory mapping is out of Phase 2")
def test_qfw_job_result_maps_counts_into_qiskit_result(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-1"])
	circuit = qfw_job.QuantumCircuit(2, name="bell")
	backend = FakeBackend()
	event_api = FakeEventAPI(events=[make_result_event("cid-1", {"00": 2, "11": 1})], fd=42)
	options = _driver_options(shots=3, seed=7, seed_simulator=13)

	def fake_select(readable, writable, exceptional, timeout):
		return (readable, [], [])

	monkeypatch.setattr(qfw_job.select, "select", fake_select)
	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)
	job.submit()
	result = job.result()

	assert result.get_counts(circuit) == {"00": 2, "11": 1}
	assert backend.dump_called is True
	assert len(backend.logged_results) == 1

	result_entry = result.data["results"][0]
	assert result_entry["header"]["name"] == "bell"
	assert result_entry["header"]["memory_slots"] == 2
	assert result_entry["shots"] == 3
	# Memory is emitted in Qiskit's hex format (QFwSamplerV2 parses it via
	# int(sample, 16)); "00" -> 0x0, "11" -> 0x3.
	assert result_entry["data"]["memory"] == ["0x0", "0x0", "0x3"]


def test_qfw_job_result_raises_when_no_results(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job
	from defw_exception import DEFwError

	fake_qpm = FakeQPM(cids=["cid-1"])
	event_api = FakeEventAPI()  # no result events ever arrive
	backend = FakeBackend()
	circuit = qfw_job.QuantumCircuit(1, name="timeout-path")
	options = _driver_options(shots=2, seed=1, seed_simulator=1)

	_stub_qasm(monkeypatch)
	# COMPLETION_TIMEOUT_SEC == 0 makes _result_reader return immediately with no
	# completed circuits -- a real timeout without the wall-clock wait.
	monkeypatch.setattr(backend, "COMPLETION_TIMEOUT_SEC", 0)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)
	job.submit()

	# With no results the per-circuit `out` never binds; result() must fail
	# with a clear error naming the cause, not a bare NameError.
	with pytest.raises(DEFwError, match="no QPM circuit results"):
		job.result()


def test_qfw_job_result_ignores_unrelated_completion_events(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-1"])
	circuit = qfw_job.QuantumCircuit(2, name="bell")
	backend = FakeBackend()
	event_api = FakeEventAPI(events=[
		make_result_event("other-cid", {"00": 1}),
		make_result_event("cid-1", {"11": 2}),
	], fd=43)
	options = _driver_options(shots=2, seed=7, seed_simulator=13)

	def fake_select(readable, writable, exceptional, timeout):
		return (readable, [], [])

	monkeypatch.setattr(qfw_job.select, "select", fake_select)
	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)
	job.submit()
	result = job.result()

	assert result.get_counts(circuit) == {"11": 2}
	assert job.status() == qfw_job.JobStatus.DONE
	assert len(backend.logged_results) == 1


def test_qfw_job_decodes_compact_statevector_payload():
	import qfw_qiskit.qfw_job as qfw_job
	from util.qpm.statevector import encode_statevector_payload

	backend = FakeBackend(statevector=True)
	job = qfw_job.QFwJob(
		backend,
		FakeQPM(),
		FakeEventAPI(),
		qfw_job.QuantumCircuit(1, name="statevector"),
		_driver_options(shots=1, seed=1, seed_simulator=1),
	)
	payload = encode_statevector_payload(
		[complex(1.0, 0.0), complex(0.0, 0.0)], num_qubits=1)

	counts, statevector, metadata = job._split_result_payload({
		"counts": {"0": 1},
		"statevector": payload,
	}, cid="cid-statevector")

	assert counts == {"0": 1}
	assert metadata == {}
	assert statevector.data == [complex(1.0, 0.0), complex(0.0, 0.0)]


def test_compact_statevector_payload_compresses_sparse_data():
	from util.qpm.statevector import (
		decode_statevector_payload,
		encode_statevector_payload,
	)

	amplitudes = [complex(0.0, 0.0)] * 1024
	amplitudes[0] = complex(1.0, 0.0)

	payload = encode_statevector_payload(amplitudes, num_qubits=10)
	decoded = decode_statevector_payload(payload)

	assert payload["encoding"] == "base64+zlib"
	assert payload["raw_size_bytes"] == 1024 * 16
	assert payload["base64_size_bytes"] < payload["raw_size_bytes"]
	assert list(decoded) == amplitudes


def test_qfw_job_result_raises_job_error_for_provider_failure(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-failed"])
	circuit = qfw_job.QuantumCircuit(1, name="provider-failure")
	backend = FakeBackend()
	event_api = FakeEventAPI(events=[make_result_event(
		"cid-failed",
		rc=-1,
		result={
			"counts": {},
			"iqm": {
				"error": "invalid CZ locus",
				"error_type": "DEFwError",
			},
		},
	)], fd=44)
	options = _driver_options(shots=2, seed=7, seed_simulator=13)

	monkeypatch.setattr(
		qfw_job.select, "select", lambda readable, *_: (readable, [], []))
	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)
	job.submit()

	with pytest.raises(qfw_job.JobError) as exc_info:
		job.result()

	message = str(exc_info.value)
	assert "cid-failed" in message
	assert "rc=-1" in message
	assert "provider=IQM" in message
	assert "error_type=DEFwError" in message
	assert "invalid CZ locus" in message
	assert job.status() == qfw_job.JobStatus.ERROR
	assert backend.dump_called is True
	assert len(backend.logged_results) == 1


def test_qfw_job_result_reports_every_failed_circuit(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-ok", "cid-iqm", "cid-generic"])
	circuits = [
		qfw_job.QuantumCircuit(1, name="ok"),
		qfw_job.QuantumCircuit(1, name="iqm-failure"),
		qfw_job.QuantumCircuit(1, name="generic-failure"),
	]
	backend = FakeBackend()
	event_api = FakeEventAPI(events=[
		make_result_event("cid-ok", {"0": 2}),
		make_result_event(
			"cid-iqm",
			rc=-1,
			result={
				"counts": {},
				"iqm": {
					"error": "delay translation failed",
					"error_type": "DEFwExecutionError",
				},
			},
		),
		make_result_event(
			"cid-generic",
			rc=3,
			result={"Error": "worker exited unexpectedly"},
		),
	], fd=45)
	options = _driver_options(shots=2, seed=7, seed_simulator=13)

	monkeypatch.setattr(
		qfw_job.select, "select", lambda readable, *_: (readable, [], []))
	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuits, options)
	job.submit()

	with pytest.raises(qfw_job.JobError) as exc_info:
		job.result()

	message = str(exc_info.value)
	assert "cid-iqm" in message
	assert "delay translation failed" in message
	assert "cid-generic" in message
	assert "worker exited unexpectedly" in message
	assert "cid-ok failed" not in message
	assert job.status() == qfw_job.JobStatus.ERROR
	assert backend.dump_called is True
	assert len(backend.logged_results) == 3


def test_qfw_job_does_not_register_per_task_completion_event(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	class RecordingBackend(FakeBackend):
		def __init__(self):
			super().__init__()
			self.registrations = []

		def register_completion_event(self, qpm, event_api, cid, response,
					      options):
			self.registrations.append({
				"qpm": qpm,
				"event_api": event_api,
				"cid": cid,
				"response": response,
				"reservation_id": options.get("reservation_id"),
			})

	response = {"cid": "cid-scoped", "qtask_id": 99}
	fake_qpm = FakeQPM(cids=[response])
	event_api = FakeEventAPI()
	backend = RecordingBackend()
	circuit = qfw_job.QuantumCircuit(1, name="scoped")
	options = {
		"shots": 2,
		"seed": 7,
		"seed_simulator": 13,
		"reservation_id": 1,
		"token": "opaque-token",
	}

	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)

	assert job._run_experiment_async(circuit) == "cid-scoped"
	assert backend.registrations == []


def test_qfw_job_submit_propagates_async_run_errors(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	backend = FakeBackend()
	fake_qpm = FakeQPM(async_error=RuntimeError("qpm submit failed"))
	event_api = FakeEventAPI()
	circuit = qfw_job.QuantumCircuit(1, name="error-path")
	options = _driver_options(shots=2, seed=1, seed_simulator=1)

	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)

	try:
		job.submit()
	except RuntimeError as exc:
		assert str(exc) == "qpm submit failed"
	else:
		raise AssertionError("expected async_run failure to propagate")
	assert job.status() == qfw_job.JobStatus.ERROR


def test_qfw_job_submit_requires_driver_reservation(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-unreserved"])
	event_api = FakeEventAPI()
	backend = FakeBackend()
	circuit = qfw_job.QuantumCircuit(1, name="unreserved")
	options = {"shots": 2, "seed": 1, "seed_simulator": 1}

	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)

	with pytest.raises(qfw_job.DEFwError, match="reservation_id is required"):
		job.submit()

	assert fake_qpm.submitted_payloads == []


def _fake_qpy(monkeypatch, dumped):
	# A qiskit.qpy the conftest stub does not carry. The job's encoder imports
	# it when it runs, so it only has to exist by then.
	import qiskit

	qpy = types.ModuleType("qiskit.qpy")
	qpy.QPY_VERSION = 16

	def dump(circuit, stream, version=None):
		dumped.append((circuit.name, version))
		stream.write(f"QPY{version}".encode("ascii"))

	qpy.dump = dump
	common = types.ModuleType("qiskit.qpy.common")
	common.QPY_COMPATIBILITY_VERSION = 13
	qpy.common = common
	monkeypatch.setattr(qiskit, "qpy", qpy, raising=False)
	monkeypatch.setitem(sys.modules, "qiskit.qpy", qpy)
	monkeypatch.setitem(sys.modules, "qiskit.qpy.common", common)


def test_qfw_job_sends_qpy_to_a_qpm_that_declares_it(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	dumped = []
	_fake_qpy(monkeypatch, dumped)
	fake_qpm = FakeQPM(cids=["cid-qpy"])
	fake_qpm.qpm_properties = {
		"circuit_formats": ["qpy", "openqasm2"],
		"qpy_version": 15,
	}
	circuit = qfw_job.QuantumCircuit(2, name="declared")
	job = qfw_job.QFwJob(
		FakeBackend(), fake_qpm, FakeEventAPI(), circuit,
		_driver_options(shots=4))
	job.submit()

	payload = fake_qpm.submitted_payloads[0]
	# Written at the version the QPM said it reads, not this client's newest.
	assert dumped == [("declared", 15)]
	assert payload["circuit"] == {
		"format": "qpy",
		"data": base64.b64encode(b"QPY15").decode("ascii"),
	}
	# Nothing calls qasm2.dumps, which is the point: a circuit OpenQASM 2
	# cannot express is no longer serialized through it.
	assert "qasm" not in payload


def test_qfw_job_still_sends_qasm_when_the_qpm_declares_nothing(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	_stub_qasm(monkeypatch, "OPENQASM 2.0; // declared nothing")
	fake_qpm = FakeQPM(cids=["cid-qasm"])
	circuit = qfw_job.QuantumCircuit(1, name="plain")
	job = qfw_job.QFwJob(
		FakeBackend(), fake_qpm, FakeEventAPI(), circuit,
		_driver_options(shots=1))
	job.submit()

	payload = fake_qpm.submitted_payloads[0]
	assert payload["qasm"] == "OPENQASM 2.0; // declared nothing"
	assert "circuit" not in payload


def test_qfw_job_sends_gzipped_qpy_when_the_qpm_declares_it(monkeypatch):
	# The whole client path, from what the QPM published to what goes on the
	# wire. The job only passes the declaration through, so this is the piece
	# that proves the wiring rather than the encoder.
	import gzip

	import qfw_qiskit.qfw_job as qfw_job

	dumped = []
	_fake_qpy(monkeypatch, dumped)
	fake_qpm = FakeQPM(cids=["cid-gzip"])
	fake_qpm.qpm_properties = {
		"circuit_formats": ["qpy+gzip", "qpy", "openqasm2"],
		"qpy_version": 16,
	}
	circuit = qfw_job.QuantumCircuit(2, name="compressed")
	job = qfw_job.QFwJob(
		FakeBackend(), fake_qpm, FakeEventAPI(), circuit,
		_driver_options(shots=4))
	job.submit()

	payload = fake_qpm.submitted_payloads[0]
	assert payload["circuit"]["format"] == "qpy+gzip"
	assert dumped == [("compressed", 16)]
	assert gzip.decompress(
		base64.b64decode(payload["circuit"]["data"])) == b"QPY16"
	assert "qasm" not in payload
