from tests.mock.fakes import FakeEventAPI, FakeQPM, make_result_event


class FakeBackend:
	COMPLETION_TIMEOUT_SEC = 5

	def __init__(self):
		self.logged_results = []
		self.dump_called = False

	def log_statistics(self, result):
		self.logged_results.append(result)

	def dump_statistics(self):
		self.dump_called = True

	def my_name(self):
		return "Fake Backend"

	def my_version(self):
		return "test-version"


def test_qfw_job_submit_builds_expected_payload(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-101"])
	fake_event_api = FakeEventAPI()
	backend = FakeBackend()
	circuit = qfw_job.QuantumCircuit(3, name="payload-circuit")
	options = {"shots": 17, "seed": 5, "seed_simulator": 11}

	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

	job = qfw_job.QFwJob(backend, fake_qpm, fake_event_api, circuit, options)
	job.submit()

	assert len(fake_qpm.submitted_payloads) == 1
	assert fake_qpm.submitted_payloads[0] == {
		"qasm": "OPENQASM 2.0;",
		"num_qubits": 3,
		"num_shots": 17,
		"compiler": "staq",
	}
	assert len(job._cid_list) == 1
	assert list(job._cid_list[0].keys()) == ["cid-101"]


def test_qfw_job_result_maps_counts_into_qiskit_result(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-1"])
	circuit = qfw_job.QuantumCircuit(2, name="bell")
	backend = FakeBackend()
	event_api = FakeEventAPI(events=[make_result_event("cid-1", {"00": 2, "11": 1})], fd=42)
	options = {"shots": 3, "seed": 7, "seed_simulator": 13}

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
	assert result_entry["data"]["memory"] == ["00", "00", "11"]


def test_qfw_job_submit_propagates_async_run_errors(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	backend = FakeBackend()
	fake_qpm = FakeQPM(async_error=RuntimeError("qpm submit failed"))
	event_api = FakeEventAPI()
	circuit = qfw_job.QuantumCircuit(1, name="error-path")
	options = {"shots": 2, "seed": 1, "seed_simulator": 1}

	monkeypatch.setattr(qfw_job.qasm2, "dumps", lambda circ: "OPENQASM 2.0;")

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)

	try:
		job.submit()
	except RuntimeError as exc:
		assert str(exc) == "qpm submit failed"
	else:
		raise AssertionError("expected async_run failure to propagate")
