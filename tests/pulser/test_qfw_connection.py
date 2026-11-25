# The Pulser client (backends/qfw_pulser) driven by real Pulser's QPUBackend:
# which sequence reaches the QPM, and how results read. Only the QPM is stood
# in for.

import dataclasses

import pytest
from pulser import Pulse, QPUBackend, Sequence
from pulser.devices import AnalogDevice
from pulser.register.special_layouts import TriangularLatticeLayout

from qfw_pulser import qfw_connection


# Two revisions of one device. The QPM serves the second, as a recalibrated
# QPU does once a program was built against the first.
BUILT_ON = dataclasses.replace(AnalogDevice, max_runs=None)
CURRENT = dataclasses.replace(AnalogDevice, max_runs=999)


class _Telemetry:
	def __init__(self):
		self.lookups = []

	def get_backend_info(self, **lookup):
		self.lookups.append(lookup)
		return {"pulser_devices": {CURRENT.name: CURRENT.to_abstract_repr()}}


class _Qpm:
	qpm_properties = {
		"provider": "shim", "circuit_formats": ["pulser-abstract-repr"]}
	expected_runtime_id = "runtime-1"

	def __init__(self):
		self.telemetry = _Telemetry()
		self.lifecycle_binding = type("Binding", (), {
			"api": lambda _self, binding, expected_runtime_id=None:
				self.telemetry})()
		self.submitted = []
		self.completions = {}

	def async_run(self, info, reservation_id=None, token=None):
		self.submitted.append(info)
		return {"cid": len(self.submitted)}

	def read_cq(self, cid=None, reservation_id=None, token=None):
		return self.completions.get(cid) or {"completion_ready": False}


@pytest.fixture
def connection(monkeypatch):
	qpm = _Qpm()
	monkeypatch.setattr(
		qfw_connection, "get_qpm", lambda *args, **kwargs: (qpm, 7))
	monkeypatch.delenv("QFW_SHIM_LIB", raising=False)
	return qfw_connection.QFwConnection(poll_interval=0.0), qpm


def _sequence():
	register = TriangularLatticeLayout(61, 5).define_register(
		0, 1, qubit_ids=("q0", "q1"))
	sequence = Sequence(register, BUILT_ON)
	sequence.declare_channel("ryd", "rydberg_global")
	sequence.add(Pulse.ConstantPulse(500, 1.0, 0, 0), "ryd")
	return sequence


def test_what_is_sent_is_the_sequence_checked_against_the_device(connection):
	# QPUBackend.run checks an updated sequence and then hands submit the
	# one it was given; the connection updates it again and sends that.
	connection, qpm = connection

	QPUBackend(_sequence(), connection=connection).run([{"runs": 100}])

	sent, = qpm.submitted
	assert Sequence.from_abstract_repr(
		sent["circuit"]["data"]).device.max_runs == 999
	assert qpm.telemetry.lookups[-1]["reservation_id"] == 7


def test_results_read_in_register_order(connection):
	connection, qpm = connection
	results = QPUBackend(_sequence(), connection=connection).run(
		[{"runs": 10}])
	qpm.completions[1] = {
		"completion_ready": True, "cid": 1, "rc": 0,
		"result": {"counts": {"10": 7, "01": 3}}}

	result, = results.results

	assert result.atom_order == ("q0", "q1")
	assert result.bitstring_counts == {"10": 7, "01": 3}
	assert result.meas_basis == "ground-rydberg"
