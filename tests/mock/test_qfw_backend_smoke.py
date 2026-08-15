from tests.mock.fakes import (
	FakeCircuit,
	FakeEventAPI,
	FakeQPM,
	FakeRuntime,
	FakeSlurmDriver,
)


def _driver_options(**options):
	return FakeSlurmDriver().execution_options(**options)


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


def test_backend_registers_event_api(monkeypatch):
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
	assert fake_qpm.registrations == [{
		"endpoint": "endpoint-1",
		"event_type": qfw_simulator.EVENT_TYPE_CIRC_RESULT,
		"class_id": "event-api-7",
	}]
	assert backend.options._validators["shots"] == (1, 65536)
	assert backend.options._validators["seed_simulator"] is int
	assert backend.options._validators["seed"] is int


def test_backend_provider_selector_uses_qpm_metadata(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator
	from api_qpm_common import QPMCapability, QPMType

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-provider")
	fake_runtime = FakeRuntime(endpoint="endpoint-provider")
	calls = []

	def fake_get_qpm(*args, **kwargs):
		calls.append((args, kwargs))
		return fake_qpm

	monkeypatch.setattr(qfw_simulator, "get_qpm", fake_get_qpm)
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)

	backend = qfw_simulator.QFwBackend(provider="nwqsim")

	assert backend.qpm is fake_qpm
	assert calls == [(
		(QPMType.QPM_TYPE_SIMULATOR, QPMCapability.QPM_CAP_STATEVECTOR),
		{"provider": "nwqsim"},
	)]
	assert backend.returns_statevector() is True


def test_backend_run_and_shutdown_leave_qpm_running(monkeypatch):
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
	assert fake_qpm.shutdown_called is False
	assert fake_runtime.exit_called is True


def test_backend_run_preserves_reservation_context(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-context")
	fake_runtime = FakeRuntime(endpoint="endpoint-context")

	monkeypatch.setattr(qfw_simulator, "get_qpm", lambda betype, capability: fake_qpm)
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)
	monkeypatch.setattr(qfw_simulator, "QFwJob", FakeJob)

	backend = qfw_simulator.QFwBackend()
	circuit = FakeCircuit(2, name="context")

	job = backend.run(
		circuit,
		shots=12,
		reservation_id=1,
		token={"opaque": "token"},
	)

	assert job.options["reservation_id"] == 1
	assert job.options["token"] == {"opaque": "token"}


def test_backend_run_uses_option_reservation_context(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-options")
	fake_runtime = FakeRuntime(endpoint="endpoint-options")

	monkeypatch.setattr(qfw_simulator, "get_qpm", lambda betype, capability: fake_qpm)
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)
	monkeypatch.setattr(qfw_simulator, "QFwJob", FakeJob)

	backend = qfw_simulator.QFwBackend()
	backend.options.reservation_id = 7
	backend.options.token = "default-token"
	backend.options.timeout = 3.5
	backend.options.cancel_on_timeout = True
	circuit = FakeCircuit(2, name="context-options")

	job = backend.run(
		circuit,
		reservation_id=8,
		timeout=1.25,
	)

	assert job.options["reservation_id"] == 8
	assert job.options["token"] == "default-token"
	assert job.options["timeout"] == 1.25
	assert job.options["cancel_on_timeout"] is True


def test_backend_registers_completion_event_once(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-scoped")
	fake_runtime = FakeRuntime(endpoint="endpoint-scoped")

	monkeypatch.setattr(qfw_simulator, "get_qpm", lambda betype, capability: fake_qpm)
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)

	backend = qfw_simulator.QFwBackend()

	backend.register_completion_events()

	assert fake_qpm.registrations == [
		{
			"endpoint": "endpoint-scoped",
			"event_type": qfw_simulator.EVENT_TYPE_CIRC_RESULT,
			"class_id": "event-api-scoped",
		}
	]


def test_qfw_job_metadata_keeps_only_qhw_result():
	from qfw_qiskit.qfw_job import QFwJob

	class FakeBackend:
		def returns_statevector(self):
			return False

	job = QFwJob(
		FakeBackend(),
		FakeQPM(),
		FakeEventAPI(),
		FakeCircuit(1),
		{"seed_simulator": 34, "shots": 12, "seed": 21},
	)
	qhw_result = {"schema": "qhw-result-v1", "timing": {}}

	counts, statevector, metadata = job._split_result_payload({
		"counts": {"0x0": 12},
		"statevector": [],
		"qhw_result": qhw_result,
		"_raw_iqm": {"job": "raw-provider-payload"},
		"iqm": {"timing_summary": {"provider": "legacy"}},
	})

	assert counts == {"0x0": 12}
	assert statevector == []
	assert metadata == {"qhw_result": qhw_result}


def test_backend_sets_qubit_mapping_metadata(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-9")
	fake_runtime = FakeRuntime(endpoint="endpoint-3")

	monkeypatch.setattr(qfw_simulator, "get_qpm", lambda betype, capability: fake_qpm)
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)

	backend = qfw_simulator.QFwBackend()
	circuit = FakeCircuit(1, name="mapped")

	mapped = backend.set_qubit_mapping(circuit, {0: "QB7"})

	assert mapped is circuit
	assert backend.get_qubit_mapping(circuit) == {"0": "QB7"}
	assert circuit.metadata == {
		"qfw": {
			"qubit_mapping": {"0": "QB7"},
		}
	}


def test_qfw_job_forwards_qubit_mapping_to_qpm():
	from qfw_qiskit.qfw_job import QFwJob
	from qfw_qiskit.qfw_metadata import set_qubit_mapping

	class FakeBackend:
		COMPLETION_TIMEOUT_SEC = 1

		def returns_statevector(self):
			return False

	fake_qpm = FakeQPM(cids=["cid-mapped"])
	circuit = FakeCircuit(1, name="mapped")
	set_qubit_mapping(circuit, {0: "QB7"})
	job = QFwJob(
		FakeBackend(),
		fake_qpm,
		FakeEventAPI(),
		circuit,
		_driver_options(seed_simulator=34, shots=12, seed=21),
	)

	cid = job._run_experiment_async(circuit)

	assert cid == "cid-mapped"
	assert fake_qpm.submitted_payloads == [
			{
				"qasm": "OPENQASM 2.0; // mapped",
				"num_qubits": 1,
				"num_shots": 12,
				"compiler": "staq",
				"qubit_mapping": {"0": "QB7"},
				"reservation_id": 1,
			}
		]


def test_qfw_job_forwards_reservation_context_to_qpm():
	from qfw_qiskit.qfw_job import QFwJob

	class FakeBackend:
		COMPLETION_TIMEOUT_SEC = 1

		def returns_statevector(self):
			return False

	fake_qpm = FakeQPM(cids=["cid-context"])
	circuit = FakeCircuit(1, name="context")
	job = QFwJob(
		FakeBackend(),
		fake_qpm,
		FakeEventAPI(),
		circuit,
		{
			"seed_simulator": 34,
			"shots": 12,
			"seed": 21,
			"reservation_id": 1,
			"token": "opaque-token",
		},
	)

	cid = job._run_experiment_async(circuit)

	assert cid == "cid-context"
	assert fake_qpm.submitted_payloads[0]["reservation_id"] == 1
	assert fake_qpm.submitted_payloads[0]["token"] == "opaque-token"


def test_qfw_job_requires_reservation_id():
	import qfw_qiskit.qfw_job as qfw_job
	from qfw_qiskit.qfw_job import QFwJob

	class FakeBackend:
		COMPLETION_TIMEOUT_SEC = 1

		def returns_statevector(self):
			return False

	job = QFwJob(
		FakeBackend(),
		FakeQPM(cids=["cid-context"]),
		FakeEventAPI(),
		FakeCircuit(1, name="context"),
		{"seed_simulator": 34, "shots": 12, "seed": 21},
	)

	try:
		job._run_experiment_async(FakeCircuit(1, name="context"))
	except qfw_job.DEFwError as exc:
		assert "reservation_id is required" in str(exc)
	else:
		raise AssertionError("expected missing reservation_id to fail")
