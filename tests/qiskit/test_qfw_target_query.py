import importlib.util
import json
import pathlib
import sys
import types

import pytest

pytest.importorskip("qiskit_ibm_runtime")
from qiskit_ibm_runtime import fake_provider  # noqa: E402

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "backends" / "qfw_qiskit"
FEZ = pathlib.Path(fake_provider.__file__).parent / "backends" / "fez"


def _load_from_file(name):
	spec = importlib.util.spec_from_file_location(
		f"qfw_qiskit.{name}", PACKAGE / f"{name}.py")
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


@pytest.fixture(scope="module")
def target_query_without_starting_defw():
	sys.modules.setdefault("qfw_qiskit", types.ModuleType("qfw_qiskit"))
	_load_from_file("qfw_target")
	return _load_from_file("qfw_target_query")


def test_a_device_document_round_trips_to_the_device_target(
		target_query_without_starting_defw):
	target_query = target_query_without_starting_defw
	configuration = json.loads((FEZ / "conf_fez.json").read_text())
	properties = json.loads((FEZ / "props_fez.json").read_text())
	document = {
		"schema": target_query.SCHEMA,
		"service_id": "fez-0123abcd",
		"provider": "aer",
		"settings": {"provider": "aer", "num_qubits": 156},
		"backend_info": {
			"backend": "fez-0123abcd",
			"metadata_supported": True,
			"static_architecture": configuration,
			"backend_properties": properties,
		},
	}

	target = target_query.target_from_json(
		json.dumps(document, default=str))

	fez = fake_provider.FakeFez().target
	assert target.num_qubits == fez.num_qubits == 156
	assert set(fez.operation_names) <= set(target.operation_names)
	assert target["cz"].keys() == fez["cz"].keys()
