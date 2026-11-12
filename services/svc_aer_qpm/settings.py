from dataclasses import dataclass, field

from defw_exception import DEFwExecutionError

MODES = ("ideal", "noisy")
MPS = "matrix_product_state"
AER_OPTIONS = {
	"method": "method",
	"device": "device",
	"max-memory-mb": "max_memory_mb",
	"target-gpus": "target_gpus",
	"mps-max-bond-dimension": "matrix_product_state_max_bond_dimension",
	"mps-truncation-threshold": "matrix_product_state_truncation_threshold",
	"mps-log-data": "mps_log_data",
}
MPS_KEYS = ("mps-max-bond-dimension", "mps-truncation-threshold", "mps-log-data")
PUBLISHED_KEYS = ("method", "device", *MPS_KEYS)


@dataclass(frozen=True)
class AerSettings:
	mode: str = "ideal"
	num_qubits: int = None
	options: dict = field(default_factory=dict)
	published: dict = field(default_factory=dict)

	@property
	def method(self):
		return self.options.get("method", "automatic")

	@property
	def device(self):
		return self.options.get("device", "CPU")


def aer_settings_from_service_entry(entry):
	block = dict(entry.get("aer") or {})
	unknown = set(block) - {"mode", "num-qubits", *AER_OPTIONS}
	if unknown:
		raise DEFwExecutionError(
			f"unknown aer settings: {', '.join(sorted(unknown))}")
	mode = str(block.pop("mode", "ideal")).lower()
	if mode not in MODES:
		raise DEFwExecutionError(
			f"aer mode must be one of {', '.join(MODES)}, not {mode!r}")
	num_qubits = block.pop("num-qubits", None)
	block.setdefault("method", "automatic")
	block["device"] = str(block.get("device", "CPU")).upper()
	if block.get("target-gpus") and block["device"] != "GPU":
		raise DEFwExecutionError("aer target-gpus needs device: GPU")
	mps_keys = sorted(set(MPS_KEYS) & set(block))
	if mps_keys and block["method"] != MPS:
		raise DEFwExecutionError(
			f"aer {', '.join(mps_keys)} needs method: {MPS}, "
			f"not {block['method']!r}")
	return AerSettings(
		mode=mode,
		num_qubits=None if num_qubits is None else int(num_qubits),
		options={AER_OPTIONS[key]: value for key, value in block.items()},
		published={
			"aer_" + key.replace("-", "_"): block[key]
			for key in PUBLISHED_KEYS if key in block})
