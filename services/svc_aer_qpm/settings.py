from dataclasses import dataclass

from defw_exception import DEFwExecutionError

MODES = ("ideal", "noisy")
DEVICES = ("CPU", "GPU")
MPS = "matrix_product_state"
MPS_KEYS = ("mps-max-bond-dimension", "mps-truncation-threshold")


@dataclass(frozen=True)
class AerSettings:
	mode: str = "ideal"
	device: str = "CPU"
	method: str = "automatic"
	max_memory_mb: int = 0
	target_gpus: tuple = ()
	num_qubits: int = None
	mps_max_bond_dimension: int = None
	mps_truncation_threshold: float = None


def aer_settings_from_service_entry(entry):
	block = dict(entry.get("aer") or {})
	unknown = set(block) - {
		"mode", "device", "method", "max-memory-mb", "target-gpus",
		"num-qubits", *MPS_KEYS}
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
	method = str(block.get("method", "automatic"))
	mps_keys = sorted(set(MPS_KEYS) & set(block))
	if mps_keys and method != MPS:
		raise DEFwExecutionError(
			f"aer {', '.join(mps_keys)} needs method: {MPS}, not {method!r}")
	num_qubits = block.get("num-qubits")
	bond_dimension = block.get("mps-max-bond-dimension")
	truncation_threshold = block.get("mps-truncation-threshold")
	return AerSettings(
		mode=mode,
		device=device,
		method=method,
		max_memory_mb=int(block.get("max-memory-mb", 0)),
		target_gpus=target_gpus,
		num_qubits=None if num_qubits is None else int(num_qubits),
		mps_max_bond_dimension=None if bond_dimension is None else int(bond_dimension),
		mps_truncation_threshold=None if truncation_threshold is None
		else float(truncation_threshold))
