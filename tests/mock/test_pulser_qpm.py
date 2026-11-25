# Guards what a QPM for a Pasqal device tells clients about itself: it runs
# Pulser sequences and nothing else, its technology is neutral-atom, and it
# advertises the neutral-atom capability bit, so the `pasqal` selection finds
# it and a lookup for a superconducting shim never does.

import pathlib
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)


def _lib_qpm(monkeypatch, provider):
	from svc_lib_qpm import descriptor as shim_descriptor
	from svc_lib_qpm.svc_qpm import QPM
	monkeypatch.delenv("QFW_QPU_DEVICE_ID", raising=False)
	monkeypatch.setattr(
		shim_descriptor, "resolve_descriptor", lambda device_id=None: {
			"provider": provider, "num_qubits": 61})
	qpm = QPM.__new__(QPM)
	qpm.controller_telemetry = lambda: {}
	return qpm


def test_a_pasqal_qpm_declares_pulser_alone(monkeypatch):
	properties = _lib_qpm(monkeypatch, "pasqal").query()["properties"]

	assert properties["circuit_formats"] == ["pulser-abstract-repr"]
	assert "qpy_version" not in properties
	assert properties["device_provider"] == "pasqal"
	assert properties["technology"] == "neutral-atom"
	assert properties["num_qubits"] == 61


@pytest.mark.parametrize("provider,capability", [
	("pasqal", "QPM_CAP_NEUTRAL_ATOM"),
	("iqm", "QPM_CAP_SUPERCONDUCTING"),
	("ibm", "QPM_CAP_SUPERCONDUCTING"),
])
def test_the_capability_bit_follows_what_the_qpm_runs(
		monkeypatch, provider, capability):
	# A client looks a QPM up by its capability bits, so a Pasqal shim must
	# not answer a lookup for a superconducting one, nor the reverse.
	from api_qpm_common import QPMCapability
	from qfw_qiskit.qpm_resolver import _bits_match
	from qfw_qiskit.qpm_selection import qpm_selection_for_provider
	info = _lib_qpm(monkeypatch, provider).query()

	assert info["qpm_capabilities"] == int(
		getattr(QPMCapability, capability))
	assert _bits_match(
		info["qpm_capabilities"],
		qpm_selection_for_provider("pasqal")["qpm_capabilities"]
	) == (provider == "pasqal")
	assert _bits_match(
		info["qpm_capabilities"],
		qpm_selection_for_provider("shim")["qpm_capabilities"]
	) == (provider != "pasqal")


# --- reading the devices as a reservation ------------------------------------

class _Controller:
	service_state = "running"

	def __init__(self, error=None, closing=False):
		self.error = error
		self.closing = closing
		self.asked = []

	def validate_reservation_for_context(self, context):
		from util.qpm.admission import QPMAdmissionValidationError
		if self.closing:
			raise QPMAdmissionValidationError(
				f"reservation is closing: "
				f"reservation_id={context.reservation_id}")

	def provider_credential_for_reservation(self, reservation_id, operation):
		self.asked.append((reservation_id, operation))
		if self.error:
			raise self.error
		return {"secret": {"token": "alice-tok", "project_id": "proj-alice"},
			"metadata": {"provider": "file"}}


def _discovery_qpm(monkeypatch, controller):
	import types
	qpm = _lib_qpm(monkeypatch, "pasqal")
	qpm.controller = controller
	qpm.qrc = types.SimpleNamespace(
		get_backend_info=lambda lib=None, credential=None: credential)
	return qpm


def test_the_devices_are_read_with_the_reservations_credential(monkeypatch):
	# Resolved by the QPM from its own bindings, never sent by the client,
	# and shaped as execution's (controller.attach_provider_credential).
	controller = _Controller()
	qpm = _discovery_qpm(monkeypatch, controller)

	assert qpm.get_backend_info(reservation_id="7") == {
		"token": "alice-tok", "project_id": "proj-alice",
		"reservation_id": 7}
	assert controller.asked[-1] == (7, "get_backend_info")
	assert qpm.get_backend_info() is None


def test_a_reservation_that_does_not_hold_is_refused(monkeypatch):
	from defw_exception import DEFwExecutionError
	from util.qpm.admission import QPMAdmissionValidationError
	qpm = _discovery_qpm(monkeypatch, _Controller(
		QPMAdmissionValidationError("invalid reservation: state=closed")))

	with pytest.raises(DEFwExecutionError, match="state=closed"):
		qpm.get_backend_info(reservation_id=7)


def test_a_closing_reservation_reads_nothing(monkeypatch):
	# Checked as for execution, so a reservation being released cannot open
	# a resource that outlives its eviction.
	from defw_exception import DEFwExecutionError
	controller = _Controller(closing=True)
	qpm = _discovery_qpm(monkeypatch, controller)

	with pytest.raises(DEFwExecutionError, match="reservation is closing"):
		qpm.get_backend_info(reservation_id=7)
	assert controller.asked == []


def test_the_credential_reaches_the_qrmi_driver(monkeypatch):
	import json
	import types
	from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver
	from svc_lib_qpm.frontend import Frontend
	opened = []
	descriptor = {
		"provider": "pasqal", "id": "pasqal-fresnel",
		"provider_device_id": "FRESNEL", "resource_type": "PasqalCloud",
		"libraries": ["qrmi"], "caps": {"get_backend_info": ["qrmi"]}}
	driver = QrmiDriver(descriptor)
	driver._qpu = lambda credential=None: opened.append(credential) or (
		types.SimpleNamespace(target=lambda: types.SimpleNamespace(
			value=json.dumps([{"device_type": "FRESNEL", "specs": "{}"}]))))
	frontend = Frontend([driver], descriptor)

	frontend.get_backend_info(credential={"reservation_id": 7})
	frontend.get_backend_info()

	assert opened == [{"reservation_id": 7}, None]
