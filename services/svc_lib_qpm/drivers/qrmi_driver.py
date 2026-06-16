# QRMI driver — routes the front-end's execution + resource-management calls
# to QRMI (IBM's Rust + C + Python resource-management interface; the `qrmi`
# Python package). QRMI owns the reservation lifecycle, so it is the default
# execution owner; it also exposes backend/target metadata, which overlaps
# with QDMI (a composable tie broken by preference).

from .base_driver import BaseDriver
from defw_exception import DEFwExecutionError
import logging


class QrmiDriver(BaseDriver):
	name = "qrmi"
	CAPABILITIES = frozenset({
		"get_backend_info",      # QRMI target/metadata (composable with QDMI)
		"run_circuit",
		"get_last_job_timing",
		"get_last_job_metadata",
	})

	def __init__(self, descriptor=None):
		# Per-resource descriptor (descriptor.py); carries device identity for
		# binding/creds and, later, dynamic capability discovery.
		self._descriptor = descriptor
		self._qrmi = None

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

	# --- execution + metadata (routing wired; hardware binding to qrmi is
	#     the next milestone — docs/qpu-frontend-contract.md §13) ---------

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
