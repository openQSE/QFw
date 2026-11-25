# Guards the Pulser frontend (backends/qfw_pulser): a Pulser RemoteConnection
# that sends each job to a QFw QPM as one Pulser sequence and reads the job's
# outcome from the QPM's completion queue.
#
# The CI mock job has no pulser, so its remote-backend API is stood in for
# here. Only the parts the connection uses are modelled.

import enum
import importlib
import json
import sys
import types

import pytest


class _BatchStatus(enum.Enum):
	PENDING = 1
	RUNNING = 2
	DONE = 3
	CANCELED = 4
	TIMED_OUT = 5
	ERROR = 6
	PAUSED = 7


class _JobStatus(enum.Enum):
	PENDING = 1
	RUNNING = 2
	DONE = 3
	CANCELED = 4
	ERROR = 5
	PAUSED = 6


class _RemoteResultsError(Exception):
	pass


class _RemoteResults:
	def __init__(self, batch_id, connection, job_ids=None):
		self.batch_id = batch_id
		self.connection = connection

	@property
	def results(self):
		return tuple(self.connection._fetch_result(self.batch_id, None))

	def get_batch_status(self):
		return self.connection._get_batch_status(self.batch_id)


class _RemoteConnection:
	@staticmethod
	def _add_measurement_to_sequence(sequence):
		return sequence

	def update_sequence_device(self, sequence):
		# Real Pulser swaps in the device fetch_available_devices returns;
		# tests/pulser runs that against real Pulser.
		return sequence


class _QPUBackend:
	@staticmethod
	def validate_job_params(job_params, max_runs):
		for params in job_params:
			if "runs" not in params:
				raise ValueError(
					"All elements of 'job_params' must specify 'runs'")


class _SampledResult:
	def __init__(self, atom_order, meas_basis, bitstring_counts):
		self.atom_order = atom_order
		self.meas_basis = meas_basis
		self.bitstring_counts = bitstring_counts


@pytest.fixture
def connection_module(monkeypatch):
	abstract_repr = types.ModuleType("pulser.abstract_repr")
	abstract_repr.deserialize_device = lambda specs: ("device", specs)
	monkeypatch.setitem(sys.modules, "pulser.abstract_repr", abstract_repr)
	remote = types.ModuleType("pulser.backend.remote")
	remote.BatchStatus = _BatchStatus
	remote.JobStatus = _JobStatus
	remote.RemoteConnection = _RemoteConnection
	remote.RemoteResults = _RemoteResults
	remote.RemoteResultsError = _RemoteResultsError
	result = types.ModuleType("pulser.result")
	result.SampledResult = _SampledResult
	pulser = types.ModuleType("pulser")
	pulser.QPUBackend = _QPUBackend
	monkeypatch.setitem(sys.modules, "pulser", pulser)
	monkeypatch.setitem(
		sys.modules, "pulser.backend", types.ModuleType("pulser.backend"))
	monkeypatch.setitem(sys.modules, "pulser.backend.remote", remote)
	monkeypatch.setitem(sys.modules, "pulser.result", result)
	monkeypatch.delenv("QFW_SHIM_LIB", raising=False)
	for name in ("qfw_pulser", "qfw_pulser.qfw_connection"):
		sys.modules.pop(name, None)
	module = importlib.import_module("qfw_pulser.qfw_connection")
	yield module
	for name in ("qfw_pulser", "qfw_pulser.qfw_connection"):
		sys.modules.pop(name, None)


class _Sequence:
	def __init__(self, parametrized=False, values=None):
		self.parametrized = parametrized
		self.values = values
		self.register = types.SimpleNamespace(qubit_ids=("q0", "q1", "q2"))
		self.device = types.SimpleNamespace(max_runs=None)

	def is_parametrized(self):
		return self.parametrized

	def is_register_mappable(self):
		return False

	def build(self, **values):
		if "missing" in values:
			raise TypeError("Did not receive values for variables: amp")
		return _Sequence(values=values)

	def get_measurement_basis(self):
		return "ground-rydberg"

	def to_abstract_repr(self):
		return json.dumps({"values": self.values})


class _Telemetry:
	def __init__(self, info):
		self.info = info
		self.lookups = []

	def get_backend_info(self, **lookup):
		self.lookups.append(lookup)
		if isinstance(self.info, Exception):
			raise self.info
		return self.info


class _Qpm:
	qpm_properties = {
		"provider": "shim", "circuit_formats": ["pulser-abstract-repr"]}
	expected_runtime_id = "runtime-1"

	def __init__(self, backend_info=None):
		self.telemetry = _Telemetry(backend_info)
		self.lifecycle_binding = types.SimpleNamespace(
			api=lambda binding, expected_runtime_id=None: (
				self.telemetry if (binding, expected_runtime_id)
				== ("telemetry", "runtime-1") else None))
		self.submitted = []
		self.cancelled = []
		# The cids whose cancel_task raises, and what it answers otherwise.
		self.cancel_fails = set()
		self.cancel_answer = None
		# What async_run raises at fail_at; RuntimeError by default.
		self.fail_with = RuntimeError("admission refused")
		# The submission that fails, counted from 1; none by default.
		self.fail_at = None
		# What read_cq answers for a cid, in turn. The last one repeats.
		self.completions = {}

	def async_run(self, info, reservation_id=None, token=None):
		if self.fail_at == len(self.submitted) + 1:
			raise self.fail_with
		self.submitted.append((info, reservation_id, token))
		return {"cid": len(self.submitted)}

	def cancel_task(self, cid=None, reservation_id=None, token=None,
			reason=None):
		if cid in self.cancel_fails:
			raise RuntimeError(f"cannot reach the QPM for {cid}")
		self.cancelled.append((cid, reason))
		return self.cancel_answer

	def read_cq(self, cid=None, reservation_id=None, token=None):
		queue = self.completions.get(cid) or [
			{"completion_ready": False, "outcome": "ACCEPTED",
				"lifecycle_state": "queued"}]
		return queue.pop(0) if len(queue) > 1 else queue[0]


def _done(cid, counts):
	return {"completion_ready": True, "cid": cid, "rc": 0,
		"result": {"counts": counts, "qhw_result": {}}}


def _connect(module, monkeypatch, qpm=None, reservation_id=7, lookups=None,
		**kwargs):
	qpm = qpm or _Qpm()

	def get_qpm(*args, **lookup):
		if lookups is not None:
			lookups.append((args, lookup))
		return qpm, reservation_id

	monkeypatch.setattr(module, "get_qpm", get_qpm)
	return module.QFwConnection(poll_interval=0.0, **kwargs), qpm


# --- connecting ----------------------------------------------------------------

def test_a_qpm_that_does_not_read_pulser_is_refused(
		connection_module, monkeypatch):
	qpm = _Qpm()
	qpm.qpm_properties = {"circuit_formats": ["qpy", "openqasm2"]}

	with pytest.raises(Exception, match="reads qpy, openqasm2, not Pulser"):
		_connect(connection_module, monkeypatch, qpm=qpm)


def test_the_lookup_asks_for_a_neutral_atom_shim(
		connection_module, monkeypatch):
	from api_qpm_common import QPMCapability, QPMType
	lookups = []

	_connect(connection_module, monkeypatch, lookups=lookups,
		service_id="shim-pasqal")

	(args, lookup), = lookups
	assert args == (
		QPMType.QPM_TYPE_HARDWARE, QPMCapability.QPM_CAP_NEUTRAL_ATOM)
	assert lookup["provider"] == "shim"
	assert lookup["service_id"] == "shim-pasqal"


def test_a_reservation_is_required(connection_module, monkeypatch):
	with pytest.raises(Exception, match="reservation_id is required"):
		_connect(connection_module, monkeypatch, reservation_id=None)


# --- submitting ----------------------------------------------------------------

def test_each_job_is_one_pulser_circuit(connection_module, monkeypatch):
	connection, qpm = _connect(
		connection_module, monkeypatch, lib="qrmi", token="tok")

	connection.submit(_Sequence(), job_params=[{"runs": 100}, {"runs": 20}])

	assert [(info["num_shots"], reservation, token) for info, reservation, token
		in qpm.submitted] == [(100, 7, "tok"), (20, 7, "tok")]
	info = qpm.submitted[0][0]
	assert info["num_qubits"] == 3
	assert info["lib"] == "qrmi"
	assert info["circuit"] == {
		"format": "pulser-abstract-repr",
		"data": json.dumps({"values": None})}


def test_a_parametrized_sequence_is_built_per_job(
		connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)

	connection.submit(_Sequence(parametrized=True), job_params=[
		{"runs": 10, "variables": {"amp": 1.0}},
		{"runs": 10, "variables": {"amp": 2.0}}])

	assert [json.loads(info["circuit"]["data"]) for info, _r, _t
		in qpm.submitted] == [
		{"values": {"amp": 1.0}}, {"values": {"amp": 2.0}}]


def test_every_job_names_its_runs(connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)

	with pytest.raises(ValueError, match="must specify 'runs'"):
		connection.submit(_Sequence(), job_params=[{"variables": {}}])
	assert qpm.submitted == []


def test_open_batches_are_not_supported(connection_module, monkeypatch):
	connection, _qpm = _connect(connection_module, monkeypatch)

	assert connection.supports_open_batch() is False
	with pytest.raises(NotImplementedError):
		connection.submit(_Sequence(), open=True, job_params=[{"runs": 1}])


# --- following the jobs ----------------------------------------------------------

def test_jobs_are_followed_to_their_results(connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	results = connection.submit(
		_Sequence(), job_params=[{"runs": 10}, {"runs": 10}])
	qpm.completions[1] = [
		{"completion_ready": False, "outcome": "ACCEPTED",
			"lifecycle_state": "submitted"},
		_done(1, {"000": 10})]

	assert results.get_batch_status() is _BatchStatus.RUNNING
	progress = connection._query_job_progress(results.batch_id)
	assert progress["1"][0] is _JobStatus.DONE
	assert progress["2"] == (_JobStatus.PENDING, None)

	qpm.completions[2] = [_done(2, {"100": 9, "000": 1})]
	assert results.get_batch_status() is _BatchStatus.DONE
	done = results.results
	assert [r.bitstring_counts for r in done] == [
		{"000": 10}, {"100": 9, "000": 1}]
	assert done[0].atom_order == ("q0", "q1", "q2")
	assert done[0].meas_basis == "ground-rydberg"


def test_wait_returns_once_the_batch_is_done(connection_module, monkeypatch):
	qpm = _Qpm()
	qpm.completions[1] = [
		{"completion_ready": False, "outcome": "ACCEPTED",
			"lifecycle_state": "submitted"},
		_done(1, {"00": 5})]
	connection, _qpm = _connect(connection_module, monkeypatch, qpm=qpm)

	results = connection.submit(_Sequence(), wait=True, job_params=[{"runs": 5}])

	assert results.get_batch_status() is _BatchStatus.DONE


def test_a_failed_job_carries_the_qpm_error(connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	results = connection.submit(_Sequence(), job_params=[{"runs": 10}])
	qpm.completions[1] = [{
		"completion_ready": True, "cid": 1, "rc": -1,
		"result": {"counts": {}, "shim": {
			"error": "QRMI task_start failed", "error_type": "DEFwError"}}}]

	assert results.get_batch_status() is _BatchStatus.ERROR
	with pytest.raises(_RemoteResultsError, match="QRMI task_start failed"):
		results.results


def test_a_cancelled_job_reads_as_cancelled(connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	results = connection.submit(_Sequence(), job_params=[{"runs": 10}])
	qpm.completions[1] = [{
		"completion_ready": True, "cid": 1, "rc": -1,
		"reason": "provider-cancelled", "result": {"counts": {}}}]

	assert results.get_batch_status() is _BatchStatus.CANCELED


def test_a_job_whose_reservation_is_gone_fails(connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	results = connection.submit(_Sequence(), job_params=[{"runs": 10}])
	qpm.completions[1] = [{
		"completion_ready": False, "outcome": "MISSING_RESERVATION",
		"reason": "reservation-not-found"}]

	assert results.get_batch_status() is _BatchStatus.ERROR
	with pytest.raises(_RemoteResultsError, match="MISSING_RESERVATION"):
		results.results


def test_a_job_the_qpm_cancels_reads_as_cancelled(
		connection_module, monkeypatch):
	# The controller's own cancel carries no rc, only the outcome.
	connection, qpm = _connect(connection_module, monkeypatch)
	results = connection.submit(_Sequence(), job_params=[{"runs": 10}])
	qpm.completions[1] = [{
		"completion_ready": True, "cid": 1, "outcome": "CANCELLED",
		"reason": "cancelled"}]

	assert results.get_batch_status() is _BatchStatus.CANCELED


def test_wait_gives_up_after_the_timeout_and_leaves_the_jobs(
		connection_module, monkeypatch):
	# The client stopping its wait is not a reason to stop the jobs: the
	# provider's own deadline is the device's job-timeout-seconds.
	qpm = _Qpm()
	qpm.completions[1] = [_done(1, {"0": 1})]
	connection, _qpm = _connect(
		connection_module, monkeypatch, qpm=qpm, timeout=0)

	with pytest.raises(
			connection_module.QFwBatchError,
			match="did not finish in 0s and is still running") as excinfo:
		connection.submit(
			_Sequence(), wait=True, job_params=[{"runs": 1}, {"runs": 1}])

	assert qpm.cancelled == []
	results = _RemoteResults(excinfo.value.batch_id, connection)
	qpm.completions[2] = [_done(2, {"1": 1})]
	assert results.get_batch_status() is _BatchStatus.DONE


def test_a_fixed_sequence_is_encoded_once(connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	sequence = _Sequence()
	writes = []
	sequence.to_abstract_repr = lambda: writes.append(1) or "{}"

	connection.submit(sequence, job_params=[{"runs": 1}] * 3)

	assert len(qpm.submitted) == 3
	assert len(writes) == 1


def test_the_qpm_serves_the_pulser_devices(connection_module, monkeypatch):
	qpm = _Qpm({"pulser_devices": {"FRESNEL": "{fresnel}", "FRESNEL_CAN1": "{can1}"}})
	connection, _qpm = _connect(
		connection_module, monkeypatch, qpm=qpm, lib="qrmi", token="tok")

	assert connection.fetch_available_devices() == {
		"FRESNEL": ("device", "{fresnel}"),
		"FRESNEL_CAN1": ("device", "{can1}")}
	# Read as the reservation, whose credential the QPM resolves itself.
	assert qpm.telemetry.lookups == [
		{"lib": "qrmi", "reservation_id": 7, "token": "tok"}]


def test_no_pulser_device_leaves_the_sequence_device(
		connection_module, monkeypatch):
	# Pulser treats NotImplementedError as "use the sequence's own device".
	qpm = _Qpm({"backend": "pasqal", "pulser_devices": {}})
	connection, _qpm = _connect(connection_module, monkeypatch, qpm=qpm)

	with pytest.raises(NotImplementedError, match="serves no Pulser device"):
		connection.fetch_available_devices()


def test_a_failure_to_fetch_the_devices_is_not_hidden(
		connection_module, monkeypatch):
	# A bad token or a network failure must not read as "no device", which
	# would skip Pulser's check against the device's specs.
	qpm = _Qpm(RuntimeError("401 Unauthorized"))
	connection, _qpm = _connect(connection_module, monkeypatch, qpm=qpm)

	with pytest.raises(RuntimeError, match="401 Unauthorized"):
		connection.fetch_available_devices()


def test_a_failed_submission_cancels_the_jobs_already_running(
		connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	qpm.fail_at = 2

	with pytest.raises(
			connection_module.QFwBatchError,
			match="after 1 of 2 jobs .admission refused.; "
			"cancellation requested for 1") as excinfo:
		connection.submit(_Sequence(), job_params=[{"runs": 1}, {"runs": 1}])

	assert isinstance(excinfo.value.__cause__, RuntimeError)
	assert qpm.cancelled == [(1, "batch submission failed")]


def test_one_cancel_failing_does_not_stop_the_others(
		connection_module, monkeypatch):
	# Each accepted job gets its own cancel, the submission failure stays
	# the cause, and the batch stays readable for what became of each job.
	connection, qpm = _connect(connection_module, monkeypatch)
	qpm.fail_at = 3
	qpm.cancel_fails = {1}

	with pytest.raises(connection_module.QFwBatchError) as excinfo:
		connection.submit(_Sequence(), job_params=[{"runs": 1}] * 3)

	message = str(excinfo.value)
	assert "admission refused" in message
	assert "cancellation requested for 2" in message
	assert "could not cancel 1: cannot reach the QPM for 1" in message
	assert isinstance(excinfo.value.__cause__, RuntimeError)
	assert qpm.cancelled == [(2, "batch submission failed")]
	assert connection._get_job_ids(excinfo.value.batch_id) == ["1", "2"]


def test_a_submission_failing_first_raises_as_it_is(
		connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	qpm.fail_at = 1

	with pytest.raises(RuntimeError, match="admission refused"):
		connection.submit(_Sequence(), job_params=[{"runs": 1}])
	assert connection._batches == {}


def test_a_wait_that_cannot_poll_hands_back_the_batch(
		connection_module, monkeypatch):
	qpm = _Qpm()
	connection, _qpm = _connect(connection_module, monkeypatch, qpm=qpm)
	read_cq = qpm.read_cq
	qpm.read_cq = lambda **kwargs: (_ for _ in ()).throw(
		ConnectionError("QPM went away"))

	with pytest.raises(
			connection_module.QFwBatchError,
			match="polling QFw batch .* failed .QPM went away.") as excinfo:
		connection.submit(_Sequence(), wait=True, job_params=[{"runs": 1}])

	assert qpm.cancelled == []
	qpm.read_cq = read_cq
	qpm.completions[1] = [_done(1, {"0": 1})]
	results = _RemoteResults(excinfo.value.batch_id, connection)
	assert [r.bitstring_counts for r in results.results] == [{"0": 1}]


def test_the_client_cancels_a_batch(connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	results = connection.submit(_Sequence(), job_params=[{"runs": 1}] * 2)
	qpm.completions[1] = [_done(1, {"0": 1})]

	connection.cancel_batch(results.batch_id)

	assert qpm.cancelled == [(2, "cancelled by the client")]


def test_a_batch_whose_completions_cannot_be_read_is_still_cancelled(
		connection_module, monkeypatch):
	# The advice after a failed poll is to cancel, so a poll failing again
	# must not stop it, and one cancel failing does not stop the others.
	connection, qpm = _connect(connection_module, monkeypatch)
	results = connection.submit(_Sequence(), job_params=[{"runs": 1}] * 2)
	qpm.read_cq = lambda **kwargs: (_ for _ in ()).throw(
		ConnectionError("QPM went away"))
	qpm.cancel_fails = {1}

	with pytest.raises(
			connection_module.QFwBatchError,
			match="cancellation requested for 2; could not cancel 1"):
		connection.cancel_batch(results.batch_id)

	assert qpm.cancelled == [(2, "cancelled by the client")]


def test_a_later_jobs_bad_variables_send_nothing(
		connection_module, monkeypatch):
	# Every job is built before any reaches the QPM, as Pasqal Cloud's own
	# connection does, so nothing runs (or is billed) and is then cancelled.
	connection, qpm = _connect(connection_module, monkeypatch)

	with pytest.raises(TypeError, match="Did not receive values"):
		connection.submit(_Sequence(parametrized=True), job_params=[
			{"runs": 1, "variables": {"amp": 1.0}},
			{"runs": 1, "variables": {"missing": 1}}])

	assert qpm.submitted == []
	assert qpm.cancelled == []


def test_an_interrupt_mid_submit_cancels_and_keeps_the_batch(
		connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	qpm.fail_at = 2
	qpm.fail_with = KeyboardInterrupt()

	with pytest.raises(KeyboardInterrupt):
		connection.submit(_Sequence(), job_params=[{"runs": 1}] * 2)

	assert qpm.cancelled == [(1, "batch submission failed")]
	batch_id, = connection._batches
	assert connection._get_job_ids(batch_id) == ["1"]


def test_a_cancel_the_qpm_refuses_is_reported(connection_module, monkeypatch):
	connection, qpm = _connect(connection_module, monkeypatch)
	results = connection.submit(_Sequence(), job_params=[{"runs": 1}])
	qpm.cancel_answer = {"outcome": "UNKNOWN", "reason": "not-found"}

	with pytest.raises(
			connection_module.QFwBatchError,
			match="could not cancel 1: the QPM answered UNKNOWN .not-found."):
		connection.cancel_batch(results.batch_id)
