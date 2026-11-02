"""QFwBackendV2 initialization, route ownership, and target tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from qfw_qiskit import QFwBackendV2, QFwJobV2
from qfw_qiskit import qfw_backend_v2


def _qhw(device_id="dummy:compiler"):
	nodes = ("QB1", "QB2")
	operations = tuple({
		"name": name,
		"arity": 2 if name == "cx" else 1,
		"parameters": ({"name": "theta"},) if name == "rz" else (),
		"supported_loci": (
			(("QB1", "QB2"),) if name == "cx"
			else (("QB1",), ("QB2",))
		),
	} for name in ("rz", "sx", "x", "cx", "measure"))
	return (
		{
			"schema": "qhw-device-v1",
			"device": {"id": device_id},
			"qubits": nodes,
		},
		{
			"schema": "qhw-coupling-v1",
			"coupling": {
				"nodes": nodes,
				"edges": (("QB1", "QB2"),),
				"directed": True,
			},
			"operations": operations,
		},
		None,
	)


def _route(device_id="dummy:compiler", compiler_id="qiskit"):
	device, coupling, calibration = _qhw(device_id)
	return SimpleNamespace(
		compiler=SimpleNamespace(info=SimpleNamespace(
			compiler_id=compiler_id)),
		compiler_input=SimpleNamespace(
			representation=(
				"python:qiskit.circuit.quantumcircuit.QuantumCircuit")),
		device=SimpleNamespace(
			device_id=device_id,
			qhw_device=device,
			qhw_coupling=coupling,
			qhw_calibration=calibration,
		),
	)


class FakeContext:
	def __init__(self, routes):
		self.routes = tuple(routes)
		self.opened = []
		self.target_requests = []

	def list_routes(self, explain=False):
		assert explain
		return SimpleNamespace(routes=self.routes, rejected=())

	def open_route(self, route):
		self.opened.append(route)
		return route

	def compiler_target(self, route):
		from libquantum_plugin_qiskit.target import qhw_to_qiskit_target
		self.target_requests.append(route)
		return qhw_to_qiskit_target(
			route.device.qhw_device,
			route.device.qhw_coupling,
			route.device.qhw_calibration,
		)


def test_v2_exports_and_builds_target_from_one_owned_route():
	context = FakeContext((_route(),))
	backend = QFwBackendV2._from_context(context)
	assert QFwBackendV2.PROVIDER_ID == "qfw.libquantum"
	assert QFwJobV2.__name__ == "QFwJobV2"
	assert context.opened == list(context.routes)
	assert context.target_requests == list(context.routes)
	assert backend.target.num_qubits == 2
	assert backend.target.operation_names == {
		"rz", "sx", "x", "cx", "measure"
	}
	assert backend.name == "qfw_v2"


def test_v2_reports_route_ambiguity_and_finalizes_partial_context(
		monkeypatch):
	context = FakeContext((_route("device-a"), _route("device-b")))
	finalized = []
	monkeypatch.setattr(
		qfw_backend_v2,
		"_try_finalize",
		lambda candidate, timeout: finalized.append((candidate, timeout)),
	)
	with pytest.raises(Exception, match="ambiguous Qiskit compiler routes"):
		QFwBackendV2._from_context(context, finalize_timeout=7)
	assert finalized == [(context, 7)]
	assert context.opened == []


@pytest.mark.parametrize("policy", ("mqt", ("mqt",), (), object()))
def test_v2_rejects_unmatched_or_invalid_route_policy(policy):
	with pytest.raises((Exception, TypeError)):
		QFwBackendV2._from_context(FakeContext((_route(),)), route_policy=policy)


def test_libquantum_source_does_not_import_qfw_sdk_adapters():
	import libquantum

	root = Path(libquantum.__file__).resolve().parent
	for source in root.glob("*.py"):
		text = source.read_text(encoding="utf-8")
		assert "qfw_backend_v2" not in text
		assert "QFwBackendV2" not in text
