from dataclasses import dataclass

from defw_exception import DEFwExecutionError

MODES = ("ideal", "noisy")
DEVICES = ("CPU", "GPU")


@dataclass(frozen=True)
class AerSettings:
	mode: str = "ideal"
	device: str = "CPU"
	method: str = "automatic"
	max_memory_mb: int = 0
	target_gpus: tuple = ()
	num_qubits: int = None


def aer_settings_from_service_entry(entry):
	block = dict(entry.get("aer") or {})
	unknown = set(block) - {
		"mode", "device", "method", "max-memory-mb", "target-gpus",
		"num-qubits"}
	if unknown:
		raise DEFwExecutionError(
			f"unknown aer settings: {', '.join(sorted(unknown))}")
	mode = str(block.get("mode", "ideal")).lower()
	if mode not in MODES:
		raise DEFwExecutionError(
			f"aer mode must be one of {', '.join(MODES)}, not {mode!r}")
	device = str(block.get("device", "CPU")).upper()
	if device not in DEVICES:
		raise DEFwExecutionError(
			f"aer device must be one of {', '.join(DEVICES)}, not {device!r}")
	target_gpus = tuple(int(gpu) for gpu in block.get("target-gpus") or ())
	if target_gpus and device != "GPU":
		raise DEFwExecutionError("aer target-gpus needs device: GPU")
	num_qubits = block.get("num-qubits")
	return AerSettings(
		mode=mode,
		device=device,
		method=str(block.get("method", "automatic")),
		max_memory_mb=int(block.get("max-memory-mb", 0)),
		target_gpus=target_gpus,
		num_qubits=None if num_qubits is None else int(num_qubits))
