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

	def returns_statevector(self):
		return False


def _driver_options(**options):
	return FakeSlurmDriver().execution_options(**options)


def test_qfw_job_submit_builds_expected_payload(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-101"])
	fake_event_api = FakeEventAPI()
	backend = FakeBackend()
	circuit = qfw_job.QuantumCircuit(3, name="payload-circuit")
	options = _driver_options(shots=17, seed=5, seed_simulator=11)

	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

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
	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

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

	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")
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
	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)
	job.submit()
	result = job.result()

	assert result.get_counts(circuit) == {"11": 2}
	assert job.status() == qfw_job.JobStatus.DONE
	assert len(backend.logged_results) == 1


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
	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

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
	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

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

	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

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

	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

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

	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)

	with pytest.raises(qfw_job.DEFwError, match="reservation_id is required"):
		job.submit()

	assert fake_qpm.submitted_payloads == []
