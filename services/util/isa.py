# See docs/design/qpu-frontend-contract.md.

import logging

# A compile job may run with no DEFw.
try:
	from defw_exception import DEFwExecutionError
except ImportError:
	DEFwExecutionError = RuntimeError

ISA_MARKER_VERSION = 1

STAGE_GENERIC = "generic"
STAGE_ISA = "isa"
DEFAULT_CIRCUIT_STAGES = (STAGE_GENERIC,)

# Braket compiles non-verbatim programs itself, and Pasqal runs Pulser sequences.
ISA_PROVIDERS = frozenset({"ibm", "iqm"})

CALIBRATION_SET_ID = "set-id"
CALIBRATION_PROPERTIES_TIME = "properties-time"
CALIBRATION_NONE = "none"
CALIBRATION_KINDS = (
	CALIBRATION_SET_ID, CALIBRATION_PROPERTIES_TIME, CALIBRATION_NONE)

POLICY_REQUIRE = "require"
POLICY_CURRENT = "current"
CALIBRATION_POLICIES = (POLICY_REQUIRE, POLICY_CURRENT)

# A set id names a calibration. A properties time only says when a device reported.
DEFAULT_CALIBRATION_POLICY = {
	CALIBRATION_SET_ID: POLICY_REQUIRE,
	CALIBRATION_PROPERTIES_TIME: POLICY_CURRENT,
}


def circuit_stages(provider=None):
	if str(provider or "").lower() in ISA_PROVIDERS:
		return [STAGE_GENERIC, STAGE_ISA]
	return list(DEFAULT_CIRCUIT_STAGES)


def _checked_calibration(calibration):
	if calibration is None:
		return {"kind": CALIBRATION_NONE, "value": None}
	if not isinstance(calibration, dict):
		raise DEFwExecutionError(
			f"an ISA marker's calibration must be a mapping: {calibration!r}")
	kind = calibration.get("kind")
	if kind not in CALIBRATION_KINDS:
		raise DEFwExecutionError(
			f"an ISA marker's calibration kind must be one of "
			f"{', '.join(CALIBRATION_KINDS)}: {kind!r}")
	value = calibration.get("value")
	if kind != CALIBRATION_NONE and value in (None, ""):
		raise DEFwExecutionError(
			f"an ISA marker's {kind} calibration needs a value")
	return {"kind": kind, "value": None if value is None else str(value)}


def make_marker(provider, device_id, calibration=None, qubit_labels=None,
		compiler=None):
	if not provider or not device_id:
		raise DEFwExecutionError("an ISA marker needs a provider and a device")
	marker = {
		"v": ISA_MARKER_VERSION,
		"provider": str(provider).lower(),
		"device_id": str(device_id),
		"calibration": _checked_calibration(calibration),
	}
	if qubit_labels is not None:
		labels = [str(label) for label in qubit_labels]
		repeated = sorted({label for label in labels if labels.count(label) > 1})
		if repeated:
			raise DEFwExecutionError(
				f"an ISA marker names these qubits twice: {repeated}")
		marker["qubit_labels"] = labels
	if compiler is not None:
		marker["compiler"] = str(compiler)
	return marker


def requested(info):
	return info.get("isa") is not None


def read_marker(info):
	marker = info.get("isa")
	if marker is None:
		return None
	if not isinstance(marker, dict):
		raise DEFwExecutionError(f"info['isa'] must be a mapping: {marker!r}")
	if marker.get("v") != ISA_MARKER_VERSION:
		raise DEFwExecutionError(
			f"this QPM reads ISA markers of version {ISA_MARKER_VERSION}, "
			f"not {marker.get('v')!r}")
	return make_marker(
		marker.get("provider"), marker.get("device_id"),
		marker.get("calibration"), marker.get("qubit_labels"),
		marker.get("compiler"))


def check_envelope(info, stages, device_id, provider):
	from util.circuit_payload import QPY_FORMATS

	marker = read_marker(info)
	if marker is None:
		return None
	if STAGE_ISA not in (stages or ()):
		raise DEFwExecutionError(
			"this QPM does not take a circuit compiled ahead of time; it "
			f"declares the stages {', '.join(stages or ())}")
	if str(provider).lower() != marker["provider"]:
		raise DEFwExecutionError(
			f"this circuit was compiled for {marker['provider']!r}, and this "
			f"QPM serves {provider!r}")
	if str(device_id) != marker["device_id"]:
		raise DEFwExecutionError(
			f"this circuit was compiled for device {marker['device_id']!r}, "
			f"and this QPM serves {device_id!r}")
	circuit = info.get("circuit")
	fmt = circuit.get("format") if isinstance(circuit, dict) else None
	if fmt not in QPY_FORMATS:
		raise DEFwExecutionError(
			"a circuit compiled ahead of time travels as QPY, because "
			f"OpenQASM 2 does not keep its native gates, not as {fmt!r}")
	if info.get("compilation_options"):
		raise DEFwExecutionError(
			"compilation_options do not apply to a circuit compiled ahead "
			"of time; a QPM runs it as it stands")
	return marker


def check_index_labels(marker):
	labels = marker.get("qubit_labels")
	if labels and labels != [str(index) for index in range(len(labels))]:
		raise DEFwExecutionError(
			"this device addresses qubits by index, so a circuit compiled for "
			f"it cannot name other qubits: {labels}")


def check_calibration(marker, current, policy=None):
	calibration = marker.get("calibration") or {}
	kind = calibration.get("kind", CALIBRATION_NONE)
	if kind == CALIBRATION_NONE:
		return
	policy = policy or DEFAULT_CALIBRATION_POLICY[kind]
	if policy not in CALIBRATION_POLICIES:
		raise DEFwExecutionError(
			f"calibration-policy must be one of "
			f"{', '.join(CALIBRATION_POLICIES)}: {policy!r}")
	compiled = calibration.get("value")
	if current is not None and str(compiled) == str(current):
		return
	message = (
		f"circuit was compiled for calibration {compiled}, and the device's "
		f"current calibration is {current}")
	if policy == POLICY_REQUIRE:
		raise DEFwExecutionError(f"{message}; compile it again")
	logging.warning(message)
