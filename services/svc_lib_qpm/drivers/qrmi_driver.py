# QRMI driver — execution/reservation owner that ALSO serves device
# introspection.
#
# QRMI (IBM's Rust + C + Python resource-management interface; the `qrmi`
# package) owns the reservation lifecycle, so it is the default execution
# owner. It also exposes the device target: `QuantumResource.target()` returns
# vendor-unique QPU configuration & properties (coupling map, basis gates,
# qubit properties; for IQM, the dynamic architecture / calibration / quality
# sets), confirmed by IBM. So introspection is NOT QDMI-exclusive — it is a
# composable facet, and for an IQM resource QRMI exposes the device as a Qiskit
# BackendV2 via `qrmi.qiskit_iqm`, the same shape QDMI-on-IQM produces.
#
# Milestone status (design doc qpu-frontend-contract.md section 13): the
# device-introspection facet's get_device_info / get_coupling_graph are wired
# to QRMI here (reusing the shared BackendV2 -> qhw normalizer), so a
# QRMI-only resource introspects through QRMI. Execution and the richer
# backend/calibration snapshots remain stubbed for a later milestone.

from .base_driver import BaseDriver
from .backend_normalize import extract_topology, to_coupling_record, to_device_record
from defw_exception import DEFwExecutionError
import logging


class QrmiDriver(BaseDriver):
	name = "qrmi"
	CAPABILITIES = frozenset({
		"get_device_info",       # QRMI target() -> device topology
		"get_coupling_graph",    # QRMI target() -> connectivity
		"get_backend_info",      # QRMI target/metadata (composable with QDMI)
		"run_circuit",
		"get_last_job_timing",
		"get_last_job_metadata",
	})

	def __init__(self, descriptor=None):
		# Per-resource descriptor (descriptor.py); carries device identity for
		# binding/creds and, later, dynamic capability discovery.
		self._descriptor = descriptor or {}
		self._qrmi = None
		self._backend_obj = None

	def _resource(self):
		# Lazy import so the service/Frontend construct and route even where
		# the qrmi package is not importable; only a real call needs it.
		if self._qrmi is None:
			try:
				import qrmi
			except Exception as exc:
				raise DEFwExecutionError(
					"failed to import qrmi. Install the qrmi Python package "
					f"before using the QRMI driver: {exc}") from exc
			self._qrmi = qrmi
			logging.debug("shim: QRMI client initialized")
		return self._qrmi

	def _backend(self):
		# Lazy: open QRMI's IQM device as a Qiskit BackendV2 once, for
		# introspection. QRMI reads its own resource config/credentials from
		# the environment (the SPANK plugin populates it inside a reservation),
		# so the provider is constructed without args, as in QRMI's own IQM
		# examples.
		if self._backend_obj is not None:
			return self._backend_obj
		provider = self._descriptor.get("provider", "iqm")
		if provider != "iqm":
			raise DEFwExecutionError(
				"QRMI introspection is wired for IQM resources via "
				f"qrmi.qiskit_iqm; provider {provider!r} is not yet supported "
				"by this driver")
		try:
			from qrmi.qiskit_iqm import IQMProvider
		except Exception as exc:
			raise DEFwExecutionError(
				"failed to import qrmi.qiskit_iqm (QRMI IQM backend). Install "
				f"QRMI with Qiskit IQM support before using QRMI "
				f"introspection: {exc}") from exc
		try:
			self._backend_obj = IQMProvider().get_backend()
		except Exception as exc:
			raise DEFwExecutionError(
				f"failed to open QRMI IQM backend: {exc}") from exc
		logging.debug("shim: QRMI IQM backend opened")
		return self._backend_obj

	def _ids(self):
		return (self._descriptor.get("provider", "iqm"),
				self._descriptor.get("id", "iqm-device"))

	# --- introspection facet: real QRMI calls (section 13 milestone) -----

	def get_device_info(self):
		provider, device_id = self._ids()
		topo = extract_topology(self._backend())
		return to_device_record(topo, provider, device_id)

	def get_coupling_graph(self, calibration_set_id=None):
		provider, device_id = self._ids()
		topo = extract_topology(self._backend())
		return to_coupling_record(topo, provider, device_id)

	# --- execution + metadata (routing wired; binding to qrmi is a later
	#     milestone — docs/qpu-frontend-contract.md section 13) -----------

	def get_backend_info(self):
		self._resource()
		return self._pending("get_backend_info", "qrmi")

	def run_circuit(self, circuit):
		self._resource()
		return self._pending("run_circuit", "qrmi")

	def get_last_job_timing(self, cid=None):
		self._resource()
		return self._pending("get_last_job_timing", "qrmi")

	def get_last_job_metadata(self, cid=None):
		self._resource()
		return self._pending("get_last_job_metadata", "qrmi")
