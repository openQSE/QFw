# QDMI driver — routes the front-end's device-introspection calls to QDMI
# (IQM's QDMI-on-IQM implementation, exposed as the `iqm.qdmi` Python package).
#
# QDMI is session-based and strong on device & calibration introspection, so
# this driver declares the introspection calls. Execution/job calls are left
# to QRMI (the reservation owner) in the default wiring.

from .base_driver import BaseDriver
from defw_exception import DEFwExecutionError
import logging


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
		self._descriptor = descriptor
		self._qdmi = None

	def _client(self):
		# Lazy import so the service/Frontend construct and route even where
		# iqm-qdmi is not importable; only a real call needs the library.
		if self._qdmi is None:
			try:
				from iqm import qdmi
			except Exception as exc:
				raise DEFwExecutionError(
					"failed to import iqm.qdmi (QDMI-on-IQM). Install "
					f"iqm-qdmi before using the QDMI driver: {exc}") from exc
			self._qdmi = qdmi
			logging.debug("shim: QDMI (iqm.qdmi) client initialized")
		return self._qdmi

	# --- introspection (routing wired; hardware binding to iqm.qdmi is the
	#     next milestone — docs/qpu-frontend-contract.md §13) -------------

	def get_device_info(self):
		self._client()
		return self._pending("get_device_info", "iqm.qdmi")

	def get_coupling_graph(self, calibration_set_id=None):
		self._client()
		return self._pending("get_coupling_graph", "iqm.qdmi")

	def get_backend_info(self):
		self._client()
		return self._pending("get_backend_info", "iqm.qdmi")

	def get_dynamic_backend_info(self, calibration_set_id=None):
		self._client()
		return self._pending("get_dynamic_backend_info", "iqm.qdmi")

	def get_calibration_snapshot(self, calibration_set_id=None):
		self._client()
		return self._pending("get_calibration_snapshot", "iqm.qdmi")
