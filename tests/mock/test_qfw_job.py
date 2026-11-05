import base64
import sys
import types

import pytest

from tests.mock.fakes import (
	FakeEventAPI,
	FakeQPM,
	FakeRuntime,
	FakeSlurmDriver,
	make_result_event,
)


class FakeBackend:
	def __init__(self, statevector=False):
		self.logged_results = []
		self.dump_called = False
		self._statevector = statevector

	def returns_statevector(self):
		return self._statevector

	def log_statistics(self, result):
		timing = ("creation_time", "launch_time", "resources_consumed_time",
			"exec_time", "completion_time", "cq_enqueue_time",
			"cq_dequeue_time")
		assert all(key in result for key in timing), result
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


def _submitted_job(monkeypatch, fake_qpm, event_api, circuits, **options):
	import qfw_qiskit.qfw_job as qfw_job

	monkeypatch.setattr(
		qfw_job.select, "select",
		lambda readable, writable, exceptional, timeout: (readable, [], []))
	_stub_qasm(monkeypatch)
	job = qfw_job.QFwJob(
		FakeBackend(), fake_qpm, event_api, circuits,
		_driver_options(shots=2, seed=1, seed_simulator=1, **options))
	job.submit()
	return job


def test_qfw_job_result_names_the_circuits_unfinished_at_its_timeout(
		monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	circuits = [
		qfw_job.QuantumCircuit(1, name="done"),
		qfw_job.QuantumCircuit(1, name="late"),
	]
	fake_qpm = FakeQPM(cids=["cid-1", "cid-2"])
	event_api = FakeEventAPI(events=[make_result_event("cid-1", {"0": 2})])
	job = _submitted_job(
		monkeypatch, fake_qpm, event_api, circuits, timeout=0)

	with pytest.raises(qfw_job.JobTimeoutError, match=r"cid-2 \(late\)$"):
		job.result()
	assert job.status() == qfw_job.JobStatus.ERROR
	assert fake_qpm.cancelled == []


def test_qfw_job_cancels_the_unfinished_circuits_on_timeout_when_asked(
		monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	circuit = qfw_job.QuantumCircuit(1, name="late")
	fake_qpm = FakeQPM(cids=["cid-1"])
	job = _submitted_job(
		monkeypatch, fake_qpm, FakeEventAPI(), circuit,
		timeout=0, cancel_on_timeout=True)

	with pytest.raises(qfw_job.JobTimeoutError, match="1 of 1 circuit"):
		job.result()
	assert fake_qpm.cancelled == [{
		"cid": "cid-1", "reservation_id": 1, "token": None,
		"reason": "job-timeout",
	}]


def test_qfw_job_without_a_timeout_waits_for_every_circuit(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	class SlowEventAPI(FakeEventAPI):
		def __init__(self, batches):
			super().__init__()
			self.batches = list(batches)

		def get(self):
			return self.batches.pop(0) if self.batches else []

	circuits = [
		qfw_job.QuantumCircuit(1, name="first"),
		qfw_job.QuantumCircuit(1, name="second"),
	]
	event_api = SlowEventAPI(
		[[], [make_result_event("cid-1", {"0": 2})], [], [],
		 [make_result_event("cid-2", {"1": 2})]])
	job = _submitted_job(
		monkeypatch, FakeQPM(cids=["cid-1", "cid-2"]), event_api, circuits)

	result = job.result()

	assert result.get_counts() == [{"0": 2}, {"1": 2}]
	assert job.status() == qfw_job.JobStatus.DONE


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


def test_qfw_job_result_raises_job_error_for_a_qpm_side_failure(monkeypatch):
	# A task the QPM itself fails, before or around the provider, completes
	# with outcome FAILED and the cause under "error", and no rc or result
	# (controller._fail_scheduled_task_locked).
	import qfw_qiskit.qfw_job as qfw_job
	from tests.mock.fakes import FakeEvent

	fake_qpm = FakeQPM(cids=["cid-failed"])
	circuit = qfw_job.QuantumCircuit(1, name="qpm-failure")
	backend = FakeBackend()
	event_api = FakeEventAPI(events=[FakeEvent({
		"cid": "cid-failed",
		"outcome": "FAILED",
		"reason": "provider-submission-failed",
		"error": {
			"reason": "provider-submission-failed",
			"error": "401 Unauthorized",
			"error_type": "DEFwExecutionError",
		},
		"completion_ready": True,
		"cq_enqueue_time": 1.5,
		"cq_dequeue_time": -1,
	})], fd=44)
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
	assert "provider-submission-failed" in message
	assert "error_type=DEFwExecutionError" in message
	assert "401 Unauthorized" in message
	assert job.status() == qfw_job.JobStatus.ERROR


def test_qfw_job_result_raises_job_error_for_a_cancelled_task(monkeypatch):
	# A task cancelled by the QPM, for instance when its reservation is
	# released, completes with outcome CANCELLED and a reason, and no rc,
	# result or timing (controller._task_status_locked).
	import qfw_qiskit.qfw_job as qfw_job
	from tests.mock.fakes import FakeEvent

	fake_qpm = FakeQPM(cids=["cid-cancelled"])
	circuit = qfw_job.QuantumCircuit(1, name="qpm-cancel")
	event_api = FakeEventAPI(events=[FakeEvent({
		"cid": "cid-cancelled",
		"outcome": "CANCELLED",
		"reason": "reservation-released",
		"completion_ready": True,
	})], fd=44)
	options = _driver_options(shots=2, seed=7, seed_simulator=13)

	monkeypatch.setattr(
		qfw_job.select, "select", lambda readable, *_: (readable, [], []))
	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(FakeBackend(), fake_qpm, event_api, circuit, options)
	job.submit()

	with pytest.raises(qfw_job.JobError, match="reservation-released"):
		job.result()
	assert job.status() == qfw_job.JobStatus.ERROR


def test_qfw_job_result_names_the_circuit_runner_failure(monkeypatch):
	# A simulator QPM reports a runner that exited non-zero as its output in
	# a "{result: ...}" string. The client used to show only the rc, and an
	# MPI launch that could not be placed read as "failed (rc=77)".
	import qfw_qiskit.qfw_job as qfw_job

	fake_qpm = FakeQPM(cids=["cid-runner"])
	circuit = qfw_job.QuantumCircuit(1, name="runner-failure")
	backend = FakeBackend()
	event_api = FakeEventAPI(events=[make_result_event(
		"cid-runner",
		rc=77,
		result=(
			"{result: \n"
			"[slurmctld:06339] PMIx_Spawn failed (-179): "
			"PMIX_ERR_JOB_FAILED_TO_MAP\n"
			"------------------------------------------------\n"
			"Your job has requested more processes than the ppr for\n"
			"}"),
	)], fd=46)
	options = _driver_options(shots=2, seed=7, seed_simulator=13)

	monkeypatch.setattr(
		qfw_job.select, "select", lambda readable, *_: (readable, [], []))
	_stub_qasm(monkeypatch)

	job = qfw_job.QFwJob(backend, fake_qpm, event_api, circuit, options)
	job.submit()

	with pytest.raises(qfw_job.JobError) as exc_info:
		job.result()

	message = str(exc_info.value)
	assert "rc=77" in message
	assert message.endswith(
		"[slurmctld:06339] PMIx_Spawn failed (-179): "
		"PMIX_ERR_JOB_FAILED_TO_MAP")


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


# --- param_values / compilation_options passthrough --------------------------

PASSTHROUGH_PARAM_VALUES = {"th[0]": 0.1, "th[1]": 0.2}
PASSTHROUGH_COMPILATION_OPTIONS = {"optimization_level": 1}


def _submit_with_options(monkeypatch, **options):
	import qfw_qiskit.qfw_job as qfw_job

	_stub_qasm(monkeypatch)
	fake_qpm = FakeQPM(cids=["cid-passthrough"])
	circuit = qfw_job.QuantumCircuit(1, name="passthrough")
	job = qfw_job.QFwJob(
		FakeBackend(), fake_qpm, FakeEventAPI(), circuit,
		_driver_options(shots=4, **options))
	job.submit()
	return fake_qpm.submitted_payloads[0]


def test_qfw_job_forwards_param_values_and_compilation_options(monkeypatch):
	payload = _submit_with_options(
		monkeypatch,
		param_values=PASSTHROUGH_PARAM_VALUES,
		compilation_options=PASSTHROUGH_COMPILATION_OPTIONS)

	assert payload["param_values"] == PASSTHROUGH_PARAM_VALUES
	assert payload["compilation_options"] == PASSTHROUGH_COMPILATION_OPTIONS


def test_qfw_job_forwards_each_passthrough_option_on_its_own(monkeypatch):
	only_params = _submit_with_options(
		monkeypatch, param_values=PASSTHROUGH_PARAM_VALUES)
	assert only_params["param_values"] == PASSTHROUGH_PARAM_VALUES
	assert "compilation_options" not in only_params

	only_compile = _submit_with_options(
		monkeypatch, compilation_options=PASSTHROUGH_COMPILATION_OPTIONS)
	assert only_compile["compilation_options"] == \
		PASSTHROUGH_COMPILATION_OPTIONS
	assert "param_values" not in only_compile


def test_qfw_job_leaves_passthrough_options_out_when_unset(monkeypatch):
	payload = _submit_with_options(monkeypatch)
	assert "param_values" not in payload
	assert "compilation_options" not in payload


def test_qfw_job_leaves_passthrough_options_out_when_none(monkeypatch):
	payload = _submit_with_options(
		monkeypatch, param_values=None, compilation_options=None)
	assert "param_values" not in payload
	assert "compilation_options" not in payload


def test_qfw_job_forwards_falsy_passthrough_values(monkeypatch):
	# Only None means "not set": an empty dict is still the caller's value.
	payload = _submit_with_options(
		monkeypatch, param_values={}, compilation_options={})
	assert payload["param_values"] == {}
	assert payload["compilation_options"] == {}


class _NamedParameter:
	# Stands in for a qiskit Parameter or ParameterVectorElement: hashable
	# and carrying a name, but not a str.
	def __init__(self, name):
		self.name = name


def test_qfw_job_keys_param_values_by_parameter_name(monkeypatch):
	payload = _submit_with_options(
		monkeypatch,
		param_values={
			_NamedParameter("th[0]"): 0.1,
			_NamedParameter("th[1]"): 0.2,
		})

	assert payload["param_values"] == {"th[0]": 0.1, "th[1]": 0.2}
	assert all(type(key) is str for key in payload["param_values"])


def test_qfw_job_accepts_mixed_name_and_parameter_keys(monkeypatch):
	payload = _submit_with_options(
		monkeypatch,
		param_values={"a": 1.0, _NamedParameter("b"): 2.0})

	assert payload["param_values"] == {"a": 1.0, "b": 2.0}


def test_qfw_job_rejects_param_values_that_is_not_a_mapping(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	with pytest.raises(qfw_job.DEFwError, match="mapping"):
		_submit_with_options(monkeypatch, param_values=[0.1, 0.2])


def test_qfw_job_rejects_param_values_with_duplicate_names(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	with pytest.raises(qfw_job.DEFwError, match="more than once"):
		_submit_with_options(
			monkeypatch,
			param_values={"th[0]": 0.1, _NamedParameter("th[0]"): 0.2})


def test_qfw_job_rejects_param_values_keys_without_a_name(monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	with pytest.raises(qfw_job.DEFwError, match="keys must be"):
		_submit_with_options(monkeypatch, param_values={0: 0.1})


def test_qfw_job_validates_param_values_before_serializing_the_circuit(
		monkeypatch):
	import qfw_qiskit.qfw_job as qfw_job

	def must_not_run(*args, **kwargs):
		raise AssertionError("the circuit was serialized first")

	monkeypatch.setattr(qfw_job, "encode_qiskit_circuit", must_not_run)

	with pytest.raises(qfw_job.DEFwError, match="mapping"):
		_submit_with_options(monkeypatch, param_values=[0.1])


def test_qfw_job_does_not_mutate_the_callers_param_values(monkeypatch):
	caller_values = {_NamedParameter("th[0]"): 0.1}
	keys_before = list(caller_values)

	_submit_with_options(monkeypatch, param_values=caller_values)

	assert list(caller_values) == keys_before


def _passthrough_backend(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	class RecordingJob:
		def __init__(self, backend, qpm, event_api, circuits, options):
			self.options = options

		def submit(self):
			pass

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (FakeQPM(), None))
	monkeypatch.setattr(
		qfw_simulator, "BaseEventAPI", lambda: FakeEventAPI())
	monkeypatch.setattr(
		qfw_simulator, "me", FakeRuntime(endpoint="endpoint-passthrough"))
	monkeypatch.setattr(qfw_simulator, "QFwJob", RecordingJob)
	return qfw_simulator.QFwBackend()


def test_backend_run_hands_passthrough_options_to_the_job(
		monkeypatch, capsys):
	backend = _passthrough_backend(monkeypatch)

	job = backend.run(
		object(), shots=8,
		param_values=PASSTHROUGH_PARAM_VALUES,
		compilation_options=PASSTHROUGH_COMPILATION_OPTIONS)

	assert job.options["param_values"] == PASSTHROUGH_PARAM_VALUES
	assert job.options["compilation_options"] == \
		PASSTHROUGH_COMPILATION_OPTIONS
	assert "is not used by this backend" not in capsys.readouterr().out


def test_backend_run_omits_passthrough_options_that_were_not_given(
		monkeypatch):
	backend = _passthrough_backend(monkeypatch)

	job = backend.run(object(), shots=8)

	assert "param_values" not in job.options
	assert "compilation_options" not in job.options


def test_backend_run_still_warns_about_unknown_options(monkeypatch, capsys):
	backend = _passthrough_backend(monkeypatch)

	backend.run(
		object(), shots=8, param_values=PASSTHROUGH_PARAM_VALUES,
		bogus_option=1)

	out = capsys.readouterr().out
	assert "bogus_option" in out
	assert "param_values" not in out
