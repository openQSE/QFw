from api_qpm_common import QPMCapability, QPMType


DEFAULT_QPM_PROVIDER = "iqm"

QPM_PROVIDER_SELECTIONS = {
	"iqm": {
		"provider": "iqm",
		"qpm_type": QPMType.QPM_TYPE_HARDWARE,
		"qpm_capabilities": QPMCapability.QPM_CAP_SUPERCONDUCTING,
	},
	"fake-iqm": {
		"provider": "fake-iqm",
		"qpm_type": QPMType.QPM_TYPE_HARDWARE,
		"qpm_capabilities": QPMCapability.QPM_CAP_SUPERCONDUCTING,
	},
	"backline": {
		"provider": "backline",
		"qpm_type": QPMType.QPM_TYPE_SIMULATOR,
		"qpm_capabilities": QPMCapability.QPM_CAP_QEC,
	},
	"shim": {
		"provider": "shim",
		"qpm_type": QPMType.QPM_TYPE_HARDWARE,
		"qpm_capabilities": QPMCapability.QPM_CAP_SUPERCONDUCTING,
	},
	# The IQM Garnet mock device, served by a shim QPM.
	"iqm-garnet-mock": {
		"provider": "shim",
		"qpm_type": QPMType.QPM_TYPE_HARDWARE,
		"qpm_capabilities": QPMCapability.QPM_CAP_SUPERCONDUCTING,
	},
	# A shim QPM serving a neutral-atom device. The Pulser client also checks
	# the QPM reads Pulser sequences (circuit_formats).
	"pasqal": {
		"provider": "shim",
		"qpm_type": QPMType.QPM_TYPE_HARDWARE,
		"qpm_capabilities": QPMCapability.QPM_CAP_NEUTRAL_ATOM,
	},
	"nwqsim": {
		"provider": "nwqsim",
		"qpm_type": QPMType.QPM_TYPE_SIMULATOR,
		"qpm_capabilities": QPMCapability.QPM_CAP_STATEVECTOR,
	},
	"tnqvm": {
		"provider": "tnqvm",
		"qpm_type": QPMType.QPM_TYPE_SIMULATOR,
		"qpm_capabilities": QPMCapability.QPM_CAP_TENSORNETWORK,
	},
	# Qiskit Aer, noiseless or emulating a named device under its noise.
	"aer": {
		"provider": "aer",
		"qpm_type": QPMType.QPM_TYPE_SIMULATOR,
		"qpm_capabilities": QPMCapability.QPM_CAP_SUPERCONDUCTING,
	},
	"qb": {
		"provider": "qb",
		"qpm_type": QPMType.QPM_TYPE_SIMULATOR,
		"qpm_capabilities": QPMCapability.QPM_CAP_STATEVECTOR,
	},
	# One service module serves both, advertising HARDWARE when a cluster
	# address resolves and SIMULATOR when none does, so the two keys select
	# between them the same way "iqm" and "fake-iqm" do. No statevector
	# capability: Qblox returns thresholded shots.
	"qblox-cluster": {
		"provider": "qblox",
		"qpm_type": QPMType.QPM_TYPE_HARDWARE,
		"qpm_capabilities": QPMCapability.QPM_CAP_SUPERCONDUCTING,
	},
	"qblox-dummy": {
		"provider": "qblox",
		"qpm_type": QPMType.QPM_TYPE_SIMULATOR,
		"qpm_capabilities": QPMCapability.QPM_CAP_SUPERCONDUCTING,
	},
}


def normalize_qpm_provider(provider, default_provider=DEFAULT_QPM_PROVIDER):
	if provider in (None, ""):
		provider = default_provider
	value = str(provider).strip().lower()
	if not value:
		value = default_provider
	if value not in QPM_PROVIDER_SELECTIONS:
		raise ValueError(f"unsupported QPM provider {provider!r}")
	return value


def qpm_selection_for_provider(
		provider=None, default_provider=DEFAULT_QPM_PROVIDER):
	provider = normalize_qpm_provider(provider, default_provider)
	return dict(QPM_PROVIDER_SELECTIONS[provider])
