# How a client compiles a circuit ahead of time for each provider. The client
# holds no provider branches, so what differs between providers is here.

from util import isa

# A compile job may run with no DEFw.
try:
	from defw_exception import DEFwExecutionError
except ImportError:
	DEFwExecutionError = RuntimeError


class DeviceNotDescribed(DEFwExecutionError):
	pass


class TargetProfile:
	compiles_against_target = True

	def read_device(self, backend):
		return {"target": backend.target, "backend_info": backend.backend_info}

	def compile(self, circuit, device, mapping=None, **options):
		from qiskit import transpile
		return transpile(circuit, target=device["target"], **options), None

	def compile_all(self, circuits, device, mappings, **options):
		from qiskit import transpile
		compiled = transpile(list(circuits), target=device["target"], **options)
		return [(circuit, None) for circuit in compiled]

	def labels_for_target(self, device):
		return None

	def validate(self, circuit, device, marker):
		from util.ibm_transcode import require_isa_circuit
		require_isa_circuit(circuit, device["target"])

	def calibration(self, device):
		info = device["backend_info"]
		if info.get("calibration_set_id"):
			return {
				"kind": isa.CALIBRATION_SET_ID,
				"value": info["calibration_set_id"],
			}
		reported = (info.get("backend_properties") or {}).get(
			"last_update_date")
		if reported:
			return {"kind": isa.CALIBRATION_PROPERTIES_TIME, "value": reported}
		return None


class IbmProfile(TargetProfile):
	def validate(self, circuit, device, marker):
		isa.check_index_labels(marker)
		super().validate(circuit, device, marker)


class IqmProfile:
	# A star device's resonator is not in a Qiskit target, so this compiles
	# against the architecture the device reports.
	compiles_against_target = False

	def read_device(self, backend):
		telemetry = backend.telemetry
		if telemetry is None or not backend.published("dynamic_architecture"):
			raise DeviceNotDescribed(
				"this QPM does not report the device's dynamic architecture")
		info = telemetry.get_dynamic_backend_info() or {}
		dynamic = info.get("dynamic_architecture")
		if not dynamic:
			raise DEFwExecutionError(
				"compiling for an IQM device needs its dynamic architecture, "
				"and this QPM did not report one")
		return {"dynamic": dynamic}

	def compile(self, circuit, device, mapping=None, **options):
		from util.iqm_transcode import architecture_backend, transpile_for_iqm
		compiled, names = transpile_for_iqm(
			circuit, architecture_backend(device["dynamic"]), mapping, **options)
		return compiled, [names[index] for index in range(compiled.num_qubits)]

	def compile_all(self, circuits, device, mappings, **options):
		return [
			self.compile(circuit, device, mapping, **options)
			for circuit, mapping in zip(circuits, mappings)]

	def labels_for_target(self, device):
		dynamic = device["dynamic"]
		if dynamic.get("computational_resonators"):
			raise DEFwExecutionError(
				"a Qiskit target cannot describe an IQM star device's "
				"resonator; compile for it with compiler='qiskit'")
		return list(dynamic.get("qubits") or ())

	def validate(self, circuit, device, marker):
		from util.iqm_transcode import build_isa_iqm_circuit
		build_isa_iqm_circuit(circuit, device["dynamic"], marker)

	def calibration(self, device):
		calibration_set_id = device["dynamic"].get("calibration_set_id")
		if not calibration_set_id:
			return None
		return {"kind": isa.CALIBRATION_SET_ID, "value": calibration_set_id}


COMPILE_PROFILES = {
	"fake-iqm": TargetProfile,
	"ibm": IbmProfile,
	"iqm": IqmProfile,
}

NOT_COMPILED_AHEAD_OF_TIME = {
	"aws": "Braket compiles a non-verbatim program itself",
	"backline": "a backline QPM does not run circuits",
	"pasqal": "Pasqal runs Pulser sequences, not gate circuits",
}


def compile_profile(provider):
	key = str(provider or "").lower()
	if key in NOT_COMPILED_AHEAD_OF_TIME:
		raise DEFwExecutionError(
			f"this device is not compiled ahead of time: "
			f"{NOT_COMPILED_AHEAD_OF_TIME[key]}")
	profile = COMPILE_PROFILES.get(key)
	return profile() if profile is not None else None
