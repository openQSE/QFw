# QDMI driver — device-introspection facet, wired to QDMI-on-IQM.
#
# QDMI-on-IQM exposes an IQM device as a Qiskit BackendV2
# (iqm.qdmi.qiskit.IQMBackend). QDMI is session-based and strong on device &
# calibration introspection, so this driver declares the introspection calls.
# Execution/job calls stay with QRMI (the reservation owner) in the default
# wiring.
#
# Milestone status (design doc qpu-frontend-contract.md section 13):
# get_device_info / get_coupling_graph normalize the device topology into the
# provider-neutral qhw schema. When QDMI-on-IQM provides raw IQM data via
# raw_device_config() (QDMI_DEVICE_PROPERTY_CUSTOM1), this driver feeds it to
# qhw-iqm -- the SAME normalizer the QRMI driver and the native path use, so both
# libraries yield one identical qhw shape (incl. IQM "QB" qubit ids). It falls
# back to normalizing the Qiskit Target (backend_normalize) when the raw
# passthrough is unavailable. The remaining introspection calls
# (backend/calibration snapshots) stay wired for routing; execution stays with
# QRMI.

from .base_driver import BaseDriver
from .backend_normalize import extract_topology, to_coupling_record, to_device_record
from defw_exception import DEFwExecutionError
import logging
import os


class QdmiDriver(BaseDriver):
	name = "qdmi"
	CAPABILITIES = frozenset({
		"get_backend_info",
		"get_device_info",
		"get_dynamic_backend_info",
		"get_calibration_snapshot",
		"get_coupling_graph",
	})

	def __init__(self, descriptor=None):
		# Per-resource descriptor (descriptor.py); carries device identity for
		# binding/creds and, later, dynamic capability discovery.
		self._descriptor = descriptor or {}
		self._backend_obj = None

	# --- QDMI session / device binding -------------------------------

	def _access(self):
		# Resolve connection settings for the QDMI-on-IQM backend. Honor the
		# same env vars the native svc_iqm_qpm uses, then fall back to the
		# shared device-access config (util.device_access). Returns the kwargs
		# IQMBackend understands (base_url / token / qc_alias).
		provider = self._descriptor.get("provider", "iqm")
		base_url = os.environ.get("QFW_QC_URL")
		token = os.environ.get("QFW_API_KEY")
		qc_alias = os.environ.get("QFW_IQM_QUANTUM_COMPUTER")
		if not (base_url and token):
			try:
				from util.device_access import resolve_device_access
				cfg = resolve_device_access(provider=provider)
			except Exception as exc:
				raise DEFwExecutionError(
					"QDMI driver could not resolve device access for "
					f"provider {provider!r}: set QFW_QC_URL/QFW_API_KEY or "
					f"configure device access: {exc}") from exc
			base_url = base_url or cfg.get("url")
			token = token or cfg.get("api_key")
			qc_alias = qc_alias or cfg.get("quantum_computer")
		return {"base_url": base_url, "token": token, "qc_alias": qc_alias}

	def _backend(self):
		# Lazy: open the QDMI-on-IQM Qiskit backend once. Import and
		# construction are deferred so the service/Frontend build and route
		# even where iqm-qdmi is absent or credentials are unset; only a real
		# introspection call needs a live backend.
		if self._backend_obj is not None:
			return self._backend_obj
		try:
			from iqm.qdmi.qiskit import IQMBackend
		except Exception as exc:
			raise DEFwExecutionError(
				"failed to import iqm.qdmi.qiskit (QDMI-on-IQM). Install "
				f"iqm-qdmi[qiskit] before using the QDMI driver: {exc}"
				) from exc
		access = self._access()
		kwargs = {key: value for key, value in access.items() if value}
		try:
			self._backend_obj = IQMBackend(**kwargs)
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to open QDMI-on-IQM backend: {exc}") from exc
		logging.debug("shim: QDMI-on-IQM backend opened (%s)",
				access.get("qc_alias") or access.get("base_url"))
		return self._backend_obj

	def _ids(self):
		return (self._descriptor.get("provider", "iqm"),
				self._descriptor.get("id", "iqm-device"))

	# --- introspection facet: raw IQM data -> qhw-iqm (section 13) --------

	def _iqm_raw(self):
		# QDMI-on-IQM publishes the raw IQM data via raw_device_config() -- the
		# same payload QRMI's target() returns -- so map it to the
		# {static_architecture, dynamic_architecture} shape qhw-iqm expects and
		# reuse that normalizer, exactly as the QRMI driver does. Returns None
		# when the raw passthrough is unavailable (older iqm-qdmi without the
		# accessor, or a device that does not populate CUSTOM1) so callers fall
		# back to normalizing the Qiskit Target.
		getter = getattr(self._backend(), "raw_device_config", None)
		if getter is None:
			return None
		try:
			target = getter()
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to read QDMI raw_device_config(): {exc}") from exc
		if not target:
			return None
		dyn = target.get("dynamic_quantum_architecture") or {}
		raw = {"dynamic_architecture": dyn}
		static = target.get("static_quantum_architecture")
		if static:
			raw["static_architecture"] = static
		return raw

	def get_device_info(self):
		provider, device_id = self._ids()
		raw = self._iqm_raw()
		if raw is not None:
			from qhw_iqm import normalize_device
			return normalize_device(raw, device_id=device_id)
		# Fallback: no raw passthrough -> normalize from the Qiskit Target.
		topo = extract_topology(self._backend())
		return to_device_record(topo, provider, device_id)

	def get_coupling_graph(self, calibration_set_id=None):
		provider, device_id = self._ids()
		raw = self._iqm_raw()
		if raw is not None:
			from qhw_iqm import normalize_coupling
			return normalize_coupling(raw, device_id=device_id)
		# Fallback: no raw passthrough -> normalize from the Qiskit Target.
		topo = extract_topology(self._backend())
		return to_coupling_record(topo, provider, device_id)

	# --- remaining introspection: routing wired, QDMI binding is a later
	#     milestone (docs/qpu-frontend-contract.md section 13) -----------

	def get_backend_info(self):
		self._backend()
		return self._pending("get_backend_info", "iqm.qdmi")

	def get_dynamic_backend_info(self, calibration_set_id=None):
		self._backend()
		return self._pending("get_dynamic_backend_info", "iqm.qdmi")

	def get_calibration_snapshot(self, calibration_set_id=None):
		self._backend()
		return self._pending("get_calibration_snapshot", "iqm.qdmi")
