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


class FakeLifecycleBinding:
	def __init__(self):
		self.listeners = []
		self.closed = False

	def add_reconnect_listener(self, listener):
		self.listeners.append(listener)

	def remove_reconnect_listener(self, listener):
		self.listeners.remove(listener)

	def reconnect(self, same_runtime):
		for listener in list(self.listeners):
			listener({"same_runtime": same_runtime})

	def close(self):
		self.closed = True


def test_backend_registers_event_api(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-7")
	fake_runtime = FakeRuntime(endpoint="endpoint-1")

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
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
		return fake_qpm, None

	monkeypatch.setattr(qfw_simulator, "get_qpm", fake_get_qpm)
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)

	backend = qfw_simulator.QFwBackend(provider="nwqsim")

	assert backend.qpm is fake_qpm
	assert calls == [(
		(QPMType.QPM_TYPE_SIMULATOR, QPMCapability.QPM_CAP_STATEVECTOR),
		{
			"provider": "nwqsim",
			"return_reservation": True,
			"service_id": None,
		},
	)]
	assert backend.returns_statevector() is True


def test_backend_run_and_shutdown_leave_qpm_running(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-8")
	fake_runtime = FakeRuntime(endpoint="endpoint-2")

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
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

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
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

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
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

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
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


def test_backend_restores_completion_event_after_same_qpm_reconnect(
		monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	lifecycle = FakeLifecycleBinding()
	fake_qpm.lifecycle_binding = lifecycle
	fake_event_api = FakeEventAPI(class_id="event-api-reconnect")
	fake_runtime = FakeRuntime(endpoint="endpoint-reconnect")

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
	monkeypatch.setattr(qfw_simulator, "BaseEventAPI", lambda: fake_event_api)
	monkeypatch.setattr(qfw_simulator, "me", fake_runtime)
	monkeypatch.setattr(qfw_simulator.g_circ_metrics, "dump", lambda: None)

	backend = qfw_simulator.QFwBackend()
	lifecycle.reconnect(same_runtime=True)
	lifecycle.reconnect(same_runtime=False)
	backend.shutdown()

	assert len(fake_qpm.registrations) == 2
	assert lifecycle.listeners == []
	assert lifecycle.closed is True


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


def test_qfw_job_reads_the_shim_result_envelope():
	# The QRMI/QDMI shim run-queue hoists a driver's qhw-result-v1 record
	# into {"counts", "qhw_result"} (svc_lib_qpm.svc_qrc._result_envelope),
	# the shape the native IQM path delivers. The client reads the counts
	# off the top and keeps the record as metadata, with no statevector key
	# present.
	from qfw_qiskit.qfw_job import QFwJob

	class FakeBackend:
		def returns_statevector(self):
			return False

	job = QFwJob(
		FakeBackend(),
		FakeQPM(),
		FakeEventAPI(),
		FakeCircuit(1),
		{"seed_simulator": 34, "shots": 10, "seed": 21},
	)
	record = {
		"schema": "qhw-result-v1",
		"provider": "aws",
		"result": {"shots": 10, "counts": {"1": 10}},
	}

	counts, statevector, metadata = job._split_result_payload(
		{"counts": {"1": 10}, "qhw_result": record})

	assert counts == {"1": 10}
	assert statevector == []
	assert metadata == {"qhw_result": record}


def test_backend_sets_qubit_mapping_metadata(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	fake_event_api = FakeEventAPI(class_id="event-api-9")
	fake_runtime = FakeRuntime(endpoint="endpoint-3")

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
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


def _backend_with_qpm_properties(monkeypatch, properties):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	fake_qpm = FakeQPM()
	if properties is not None:
		fake_qpm.qpm_properties = properties
	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (fake_qpm, None))
	monkeypatch.setattr(
		qfw_simulator, "BaseEventAPI",
		lambda: FakeEventAPI(class_id="event-api-lib"))
	monkeypatch.setattr(
		qfw_simulator, "me", FakeRuntime(endpoint="endpoint-lib"))
	monkeypatch.setattr(qfw_simulator, "QFwJob", FakeJob)
	monkeypatch.delenv(qfw_simulator.SHIM_LIB_ENV, raising=False)
	return qfw_simulator.QFwBackend()


def test_backend_run_names_the_shim_library(monkeypatch):
	backend = _backend_with_qpm_properties(monkeypatch, {"provider": "shim"})

	job = backend.run(FakeCircuit(2, name="lib"), lib="QDMI")

	assert job.options["lib"] == "qdmi"


def test_backend_lib_option_and_env_default(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	backend = _backend_with_qpm_properties(monkeypatch, {"provider": "shim"})
	monkeypatch.setenv(qfw_simulator.SHIM_LIB_ENV, "qdmi")
	circuit = FakeCircuit(2, name="lib-env")

	assert backend.run(circuit).options["lib"] == "qdmi"
	# The backend's option and an explicit run() argument both win over
	# the environment, and "default" hands the choice back to the shim.
	backend.options.lib = "qrmi"
	assert backend.run(circuit).options["lib"] == "qrmi"
	assert "lib" not in backend.run(circuit, lib="default").options


def test_backend_run_without_lib_sends_none(monkeypatch):
	backend = _backend_with_qpm_properties(monkeypatch, {"provider": "shim"})

	job = backend.run(FakeCircuit(2, name="no-lib"))

	assert "lib" not in job.options


def test_backend_rejects_an_unknown_shim_library(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	backend = _backend_with_qpm_properties(monkeypatch, {"provider": "shim"})

	try:
		backend.run(FakeCircuit(2, name="bad-lib"), lib="qiskit")
	except qfw_job.DEFwError as exc:
		assert "lib must be one of qrmi, qdmi" in str(exc)
	else:
		raise AssertionError("expected an unknown library to fail")


def test_backend_rejects_lib_for_a_qpm_that_is_not_the_shim(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	backend = _backend_with_qpm_properties(
		monkeypatch, {"provider": "nwqsim"})

	try:
		backend.run(FakeCircuit(2, name="lib-nwqsim"), lib="qdmi")
	except qfw_job.DEFwError as exc:
		assert "'nwqsim', not the shim" in str(exc)
	else:
		raise AssertionError("expected lib on a non-shim QPM to fail")


def test_qfw_job_forwards_shim_library_to_qpm():
	from qfw_qiskit.qfw_job import QFwJob

	class FakeBackend:
		COMPLETION_TIMEOUT_SEC = 1

		def returns_statevector(self):
			return False

	fake_qpm = FakeQPM(cids=["cid-lib"])
	circuit = FakeCircuit(1, name="lib")
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
			"lib": "qdmi",
		},
	)

	assert job._run_experiment_async(circuit) == "cid-lib"
	assert fake_qpm.submitted_payloads[0]["lib"] == "qdmi"
