"""Phase 19 isolated QFwBackendV2 and Qiskit primitive gate."""

import importlib.util
import os
from pathlib import Path
import sys

from qiskit import QuantumCircuit
from qiskit.primitives import BackendEstimatorV2, BackendSamplerV2
from qiskit.quantum_info import SparsePauliOp

import libquantum as lq
from libquantum._handles import _initialize_with_runtime
from libquantum_plugin_qiskit import plugin_path
from qfw_qiskit import QFwBackendV2


def _dummy_qri():
	qfw_root = Path(__file__).resolve().parents[2]
	libquantum_source = Path(os.environ.get(
		"LIBQUANTUM_SOURCE", qfw_root.parent / "libquantum"))
	path = libquantum_source / "tests" / "fixtures" / "dummy_qri.py"
	spec = importlib.util.spec_from_file_location(
		"libquantum_phase_19_dummy_qri", path)
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


def _qhw(device_id):
	nodes = ("QB1", "QB2")
	operations = []
	for name, parameters in (
		("rz", ({"name": "theta"},)),
		("sx", ()),
		("x", ()),
		("measure", ()),
	):
		operations.append({
			"name": name,
			"arity": 1,
			"parameters": parameters,
			"supported_loci": tuple((node,) for node in nodes),
		})
	operations.append({
		"name": "cx",
		"arity": 2,
		"supported_loci": (("QB1", "QB2"),),
	})
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
			"operations": tuple(operations),
		},
		None,
	)


def _backend():
	dummy = _dummy_qri()
	runtime = dummy.DummyRuntime()
	runtime._reservations = (dummy.DummyReservation("qpm-a", "101"),)
	qpm = runtime.qpms["qpm-a"]
	qpm.device_id = "dummy:compiler"
	qpm.inputs = ({
		"representation": "openqasm",
		"versions": ["2.0"],
		"preference": 0,
		"native": True,
	},)
	qpm.qhw_device, qpm.qhw_coupling, qpm.qhw_calibration = _qhw(
		qpm.device_id)
	original_async_run = qpm.async_run

	def complete_immediately(info, **options):
		response = original_async_run(info, **options)
		shots = options.get("shots") or 1
		qpm.complete(response["cid"], {
			"counts": {"0x0": shots},
			"memory": ["0x0"] * shots,
			"shots": shots,
		})
		return response

	qpm.async_run = complete_immediately
	context = _initialize_with_runtime(
		lq.InitConfig(
			compiler_paths=(plugin_path(),),
			enabled_compilers=frozenset({"qiskit"}),
		),
		runtime,
	)
	return QFwBackendV2._from_context(context), qpm


def _bell(measure=True):
	circuit = QuantumCircuit(2, 2 if measure else 0, name="bell")
	circuit.h(0)
	circuit.cx(0, 1)
	if measure:
		circuit.measure((0, 1), (0, 1))
	return circuit


def test_backend_and_qiskit_primitives_use_only_qiskit_public_objects():
	backend, qpm = _backend()
	try:
		assert backend._context.compiler_target(backend._route) is backend.target
		direct = backend.run(_bell(), shots=8)
		assert direct.result(timeout=1).get_counts() == {"00": 8}
		sampler = BackendSamplerV2(backend=backend)
		sampler_result = sampler.run([_bell()], shots=8).result()
		assert sampler_result[0].data.c.get_counts() == {"00": 8}
		estimator = BackendEstimatorV2(backend=backend)
		estimator_result = estimator.run([
			(_bell(measure=False), SparsePauliOp("ZI")),
		], precision=0.5).result()
		assert len(estimator_result) == 1
		assert all(
			submission["options"].get("shots") is not None
			for submission in qpm.submissions
		)
		assert all(
			isinstance(submission["info"], dict)
			and submission["info"]["representation"] == "openqasm"
			for submission in qpm.submissions
		)
		assert not hasattr(backend, "qpm")
		assert not hasattr(direct, "op_id")
		assert backend._context.compiler_target(backend._route) is backend.target
		assert len(backend._context._compiler_environment._target_cache) == 1
	finally:
		assert backend.shutdown(timeout=0).status is lq.FinalizeStatus.FINALIZED
