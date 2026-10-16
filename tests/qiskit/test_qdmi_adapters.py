# Real released adapters and registry, without device initialization or jobs.
import pytest

from amazon.braket.qdmi import qiskit as braket
from ibm.qdmi import qiskit as ibm
from iqm.qdmi import qiskit as iqm
from mqt.core.plugins.qiskit.backend import QDMIBackend


@pytest.mark.parametrize("provider", ["iqm", "braket", "ibm"])
def test_repeated_backend_construction_keeps_separate_sessions(monkeypatch, provider):
	opened = []
	devices = []

	def open_device(device_id, **kwargs):
		device = object()
		opened.append((device_id, kwargs))
		devices.append(device)
		return device

	# Stub network/session opening and Target construction, not registration.
	monkeypatch.setattr(QDMIBackend, "__init__",
		lambda self, device, **kwargs: setattr(self, "_device", device))
	if provider == "iqm":
		monkeypatch.delenv("IQM_TOKENS_FILE", raising=False)
		monkeypatch.delenv("IQM_QC_ID", raising=False)
		monkeypatch.setattr(iqm, "open_device", open_device)
		backends = [iqm.IQMBackend(base_url="https://qc.example.org", token=token, qc_alias="default")
			for token in ["alice", "bob", "alice"]]
		assert [kwargs["token"] for _, kwargs in opened] == ["alice", "bob", "alice"]
	elif provider == "braket":
		monkeypatch.setattr(braket, "open_device", open_device)
		backends = [braket.AmazonBraketBackend(
			device_id="amazon.braket.default",
			device_arn="arn:aws:braket:::device/quantum-simulator/amazon/sv1",
			region=None, reservation_arn=None) for _ in range(3)]
		assert all(kwargs["custom2"] is None and kwargs["custom3"] is None
			for _, kwargs in opened)
	elif provider == "ibm":
		monkeypatch.setenv("IBM_QUANTUM_API_KEY", "ambient-key")
		monkeypatch.setenv("IBM_QUANTUM_INSTANCE_CRN", "ambient-instance")
		monkeypatch.setenv("IBM_QUANTUM_BACKEND", "ambient-backend")
		monkeypatch.setattr(ibm, "open_device", open_device)
		backends = [ibm.IBMBackend(
			api_key=token, instance_crn=instance, backend_name="ibm_test",
			base_url="https://qc.example.org/api",
			auth_url="https://iam.example.org/identity/token")
			for token, instance in [("alice", "first"), ("bob", "second"), ("alice", "first")]]
		assert [kwargs for _, kwargs in opened] == [{
			"token": token, "custom1": "ibm_test", "custom2": instance,
			"base_url": "https://qc.example.org/api",
			"auth_url": "https://iam.example.org/identity/token"}
			for token, instance in [("alice", "first"), ("bob", "second"), ("alice", "first")]]
		assert all(device_id == "ibm.default" for device_id, _ in opened)
	else:
		raise ValueError(f"Unknown provider: {provider!r}")
	assert [backend.device for backend in backends] == devices
	assert len({id(device) for device in devices}) == 3
	assert len({device_id for device_id, _ in opened}) == 1
