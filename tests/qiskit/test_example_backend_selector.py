"""Shared QFw example backend selector tests."""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


def _selector():
	path = (
		Path(__file__).resolve().parents[2]
		/ "examples" / "tests" / "qfw_example_backend.py"
	)
	spec = importlib.util.spec_from_file_location("qfw_example_backend_test", path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_selector_passes_provider_only_to_legacy(monkeypatch):
	module = _selector()
	calls = []

	class Legacy:
		def __init__(self, **kwargs):
			calls.append(("legacy", kwargs))

	class V2:
		def __init__(self, **kwargs):
			calls.append(("v2", kwargs))

	qfw = ModuleType("qfw_qiskit")
	qfw.QFwBackend = Legacy
	qfw.QFwBackendV2 = V2
	monkeypatch.setitem(sys.modules, "qfw_qiskit", qfw)
	monkeypatch.setattr(module.metadata, "version", lambda name: "test")
	assert isinstance(module.create_qfw_backend("nwqsim", "legacy"), Legacy)
	assert isinstance(module.create_qfw_backend("nwqsim", "v2"), V2)
	assert calls == [
		("legacy", {"provider": "nwqsim"}),
		("v2", {}),
	]


def test_v2_failure_never_falls_back_to_legacy(monkeypatch):
	module = _selector()

	class Legacy:
		def __init__(self, **kwargs):
			raise AssertionError("legacy fallback was attempted")

	class V2:
		def __init__(self):
			raise RuntimeError("v2 failed")

	qfw = ModuleType("qfw_qiskit")
	qfw.QFwBackend = Legacy
	qfw.QFwBackendV2 = V2
	monkeypatch.setitem(sys.modules, "qfw_qiskit", qfw)
	with pytest.raises(RuntimeError, match="v2 failed"):
		module.create_qfw_backend("nwqsim", "v2")


def test_backend_api_flag_is_removed_before_positional_parsing(monkeypatch):
	module = _selector()
	monkeypatch.setattr(
		sys,
		"argv",
		["example.py", "4", "--backend-api", "v2", "nwqsim"],
	)
	assert module.backend_api_from_argv() == "v2"
	assert sys.argv == ["example.py", "4", "nwqsim"]


def test_every_qiskit_backend_example_uses_shared_selector():
	root = Path(__file__).resolve().parents[2] / "examples" / "tests"
	for path in (*root.glob("test_qiskit*.py"), root / "test_init_qfw.py"):
		text = path.read_text(encoding="utf-8")
		assert "QFwBackend(" not in text
		assert "qfw_example_backend" in text
