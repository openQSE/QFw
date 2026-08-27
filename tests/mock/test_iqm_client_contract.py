import sys
import types

import pytest

from defw_exception import DEFwExecutionError
from svc_iqm_qpm import util_iqm


def install_iqm_client_module(monkeypatch, client_type):
	iqm_package = types.ModuleType("iqm")
	iqm_package.__path__ = []
	client_module = types.ModuleType("iqm.iqm_client")
	client_module.IQMClient = client_type
	monkeypatch.setitem(sys.modules, "iqm", iqm_package)
	monkeypatch.setitem(sys.modules, "iqm.iqm_client", client_module)


def test_iqm_client_contract_accepts_required_version(monkeypatch):
	class FakeIQMClient:
		pass

	monkeypatch.setattr(
		util_iqm, "package_version",
		lambda package: util_iqm.REQUIRED_IQM_CLIENT_VERSION)
	install_iqm_client_module(monkeypatch, FakeIQMClient)

	assert util_iqm.load_iqm_client_module() is FakeIQMClient


def test_iqm_client_contract_rejects_other_version(monkeypatch):
	monkeypatch.setattr(util_iqm, "package_version", lambda package: "28.0.0")

	with pytest.raises(DEFwExecutionError) as exc_info:
		util_iqm.load_iqm_client_module()

	assert "unsupported iqm-client version 28.0.0" in str(exc_info.value)
	assert "iqm-client==34.0.1" in str(exc_info.value)


def test_iqm_client_contract_rejects_missing_package(monkeypatch):
	def missing_package(package):
		raise util_iqm.PackageNotFoundError(package)

	monkeypatch.setattr(util_iqm, "package_version", missing_package)

	with pytest.raises(DEFwExecutionError) as exc_info:
		util_iqm.load_iqm_client_module()

	assert "iqm-client is not installed" in str(exc_info.value)
	assert "iqm-client==34.0.1" in str(exc_info.value)


def test_iqm_service_checks_contract_during_initialization(monkeypatch):
	class FakeIQMClient:
		pass

	loads = []

	def load_client():
		loads.append(True)
		return FakeIQMClient

	monkeypatch.setattr(util_iqm, "load_iqm_client_module", load_client)

	service = util_iqm.IQMServiceClient()

	assert loads == [True]
	assert service._client_type is FakeIQMClient


def test_iqm_client_creation_uses_required_api():
	created = {}

	class FakeIQMClient:
		def __init__(self, url, *, token, quantum_computer):
			created.update({
				"url": url,
				"token": token,
				"quantum_computer": quantum_computer,
			})

	util_iqm.create_iqm_client(FakeIQMClient, {
		"url": "https://iqm.invalid",
		"api_key": "protected-token",
		"quantum_computer": "qpu-1",
	})

	assert created == {
		"url": "https://iqm.invalid",
		"token": "protected-token",
		"quantum_computer": "qpu-1",
	}
