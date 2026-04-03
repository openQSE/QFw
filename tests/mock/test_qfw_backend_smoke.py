from tests.mock.fakes import FakeCircuit, FakeEventAPI, FakeQPM, FakeRuntime


class FakeJob:
	def __init__(self, backend, qpm, event_api, circuits, options):
		self.backend = backend
		self.qpm = qpm
		self.event_api = event_api
		self.circuits = circuits
		self.options = options
		self.submit_called = False

	def submit(self):
		self.submit_called = True


def test_backend_registers_event_api_and_qpm(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-7")
	fake_runtime = FakeRuntime(endpoint="endpoint-1")

	monkeypatch.setattr(qfw_simulator, "get_qpm", lambda betype, capability: fake_qpm)
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)

	backend = qfw_simulator.QFwBackend()

	assert backend.qpm is fake_qpm
	assert backend.event_api is fake_event_api
	assert fake_event_api.registered is True
	assert fake_qpm.registrations == [
		{
			"endpoint": "endpoint-1",
			"event_type": qfw_simulator.EVENT_TYPE_CIRC_RESULT,
			"class_id": "event-api-7",
		}
	]
	assert backend.options._validators["shots"] == (1, 65536)
	assert backend.options._validators["seed_simulator"] is int
	assert backend.options._validators["seed"] is int


def test_backend_run_and_shutdown_use_job_and_runtime(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-8")
	fake_runtime = FakeRuntime(endpoint="endpoint-2")

	monkeypatch.setattr(qfw_simulator, "get_qpm", lambda betype, capability: fake_qpm)
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)
	monkeypatch.setattr(qfw_simulator, "QFwJob", FakeJob)
	monkeypatch.setattr(qfw_simulator.g_circ_metrics, "dump", lambda: None)

	backend = qfw_simulator.QFwBackend()
	circuit = FakeCircuit(2, name="smoke")

	job = backend.run(circuit, shots=12, seed=21, seed_simulator=34)
	backend.shutdown()

	assert isinstance(job, FakeJob)
	assert job.circuits is circuit
	assert job.submit_called is True
	assert job.options == {"seed_simulator": 34, "shots": 12, "seed": 21}
	assert fake_qpm.shutdown_called is True
	assert fake_runtime.exit_called is True
