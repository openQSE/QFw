"""Compile Qiskit circuits with the MQT compiler (mqt.core.mlir)."""

from qiskit import QuantumCircuit
from mqt.core import mlir

class MQTCompileError(RuntimeError):
	pass

def mqt_compile(circuits, target):
	"""Compile circuits for the device described by ``target``.

	``target`` is the Qiskit Target provided by QFwBackend.target, whose
	description names the device.
	"""

	single = isinstance(circuits, QuantumCircuit)
	circuit_list = [circuits] if single else list(circuits)
	device_name = target.description

	try:
		compiled = [
			mlir.compile_program(circuit, target=target)
			for circuit in circuit_list]
	except Exception as exc:
		raise MQTCompileError(
			f"MQT compile for {device_name} failed: {exc}") from exc
	return compiled[0] if single else compiled
