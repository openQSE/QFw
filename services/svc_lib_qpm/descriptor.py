# Per-resource capability descriptor (design doc qpu-frontend-contract.md
# section 5). Capability belongs to the resource-plus-library leaf, not to a
# library as a whole — so the Frontend is built per resource from one of these.
#
# Today resolve_descriptor() returns a built-in default for the IQM q20.
# The intended source is dev-config/config.yaml (per device_id), resolved via
# services/util/device_access.py (design doc section 10); dynamic per-resource
# refinement (querying the library at bind time) is the open decision in
# section 12.2. The default keeps the routing layer descriptor-driven now.

import logging
import os

QPU_DEVICE_ENV = "QFW_QPU_DEVICE_ID"

# Default descriptor for the ORNL IQM q20. Device introspection is a
# COMPOSABLE facet: both QRMI (via QuantumResource.target()) and QDMI serve it.
# The preference is a configurable tiebreaker (default QDMI here, env-
# overridable) -- introspection is not reservation-bound for either. Execution
# is QRMI's (the execution owner). caps[call] = libraries that cover that call
# FOR THIS RESOURCE. A list
# of length > 1 is a composable overlap broken by preference; an empty list (or
# missing key) is a NULL-out / gap-map entry.
#
# get_calibration_snapshot is COMPOSABLE: QRMI serves it from target()'s IQM
# calibration/quality sets, and QDMI serves it from FoMaC's live T1/T2 + gate
# fidelity (different populated fields, same qhw-calibration-v1 shape). Preference
# (QDMI) wins by default. get_dynamic_backend_info / get_backend_info stay
# QRMI-only: their native shape carries raw IQM architecture data that QDMI's
# neutral model does not expose.
_DEFAULT_DESCRIPTORS = {
	"ornl-iqm-20q": {
		"id": "ornl-iqm-20q",
		"provider": "iqm",
		"libraries": ["qrmi", "qdmi"],
		"preference": "qdmi",
		"execution_owner": "qrmi",
		"caps": {
			"get_device_info":          ["qdmi", "qrmi"],
			"get_coupling_graph":       ["qdmi", "qrmi"],
			"get_calibration_snapshot": ["qdmi", "qrmi"],
			"get_dynamic_backend_info": ["qrmi"],
			"get_backend_info":         ["qrmi"],
			"run_circuit":              ["qrmi"],
			"get_last_job_timing":      ["qrmi"],
			"get_last_job_metadata":    ["qrmi"],
		},
	},
}

# Fallback for an unknown device id: wire both libraries with the same coarse
# introspection-QDMI / execution-QRMI split.
_FALLBACK = _DEFAULT_DESCRIPTORS["ornl-iqm-20q"]


def resolve_descriptor(device_id=None):
	"""Return the per-resource descriptor for `device_id`.

	TODO: read libraries/preference/caps from dev-config/config.yaml per
	device_id (design doc section 10), and optionally refine via dynamic
	discovery (section 12.2). For now this returns a built-in default so the
	Frontend is descriptor-driven from the start.
	"""
	device_id = device_id or os.environ.get(QPU_DEVICE_ENV, "ornl-iqm-20q")
	desc = _DEFAULT_DESCRIPTORS.get(device_id)
	if desc is None:
		logging.debug(
			"shim: no descriptor for %r; using fallback", device_id)
		desc = dict(_FALLBACK, id=device_id)
	return desc
