import importlib.util
from pathlib import Path


def _load_example_context():
	repo_root = Path(__file__).resolve().parents[2]
	module_path = repo_root / "examples" / "tests" / "qfw_example_context.py"
	spec = importlib.util.spec_from_file_location("qfw_example_context", module_path)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_qfw_reservation_options_normalizes_numeric_env_value(monkeypatch):
	context = _load_example_context()

	monkeypatch.setenv("QFW_RESERVATION_ID", "42")

	assert context.qfw_reservation_options() == {"reservation_id": 42}
