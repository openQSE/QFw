def test_estimator_options_accept_run_options():
	from qfw_qiskit.qfw_estimator import Options

	options = Options(run_options={
		"reservation_id": "reservation-1",
	})

	assert options.run_options == {
		"reservation_id": "reservation-1",
	}


def test_estimator_run_circuits_forwards_run_options():
	import qfw_qiskit.qfw_estimator as qfw_estimator

	class FakeJob:
		def result(self):
			return "result"

	class FakeBackend(qfw_estimator.BackendV2):
		max_circuits = 32

		def __init__(self):
			self.calls = []

		def run(self, circuits, **options):
			self.calls.append((circuits, options))
			return FakeJob()

	backend = FakeBackend()
	circuit = qfw_estimator.QuantumCircuit(1, name="estimator")

	results, metadata = qfw_estimator._run_circuits(
		[circuit],
		backend,
		shots=12,
		reservation_id="reservation-1",
	)

	assert results == ["result"]
	assert metadata == [{}]
	assert backend.calls[0][1]["reservation_id"] == "reservation-1"
	assert "token" not in backend.calls[0][1]
