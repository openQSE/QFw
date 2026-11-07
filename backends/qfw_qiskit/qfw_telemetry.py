# The QPM's telemetry API as a Qiskit application sees it.
#
# A QPM publishes two APIs the client cares about: the execution binding
# (api_qpm_execution, what QFwBackend runs circuits through) and the telemetry
# binding (api_qpm_telemetry, what the QPM says about its device). The
# telemetry calls are the introspection facet of the QPU frontend contract:
# get_backend_info, get_device_info, get_dynamic_backend_info,
# get_calibration_snapshot and get_coupling_graph. This wrapper puts them on
# the backend as backend.telemetry, with the run context the backend already
# holds filled in as appropriate for each telemetry call.


class QFwTelemetry:
	"""The telemetry API of the QPM a QFwBackend is bound to.

	`api` is the managed telemetry binding from the QPM lookup
	(qfw_lookup_service.get_qpm_telemetry). `context` is a callable
	returning the backend's telemetry context. The shim library and token
	are supplied to telemetry calls when present; `reservation_id` is
	supplied to `get_backend_info`, and to `get_dynamic_backend_info` when
	asked, since an older QPM's API does not accept it there. A `lib`
	passed to a call wins over the context's. The context's library is not
	given to `get_dynamic_backend_info`, which a QPM serves from whichever of
	its libraries can describe the device's live state.
	"""

	CALLS = (
		"get_backend_info",
		"get_device_info",
		"get_dynamic_backend_info",
		"get_calibration_snapshot",
		"get_coupling_graph",
	)

	def __init__(self, api, context=None):
		self._api = api
		self._context = context

	def _arguments(self, lib, include_reservation=False,
		       include_library=True, **extra):
		arguments = dict(self._context() if self._context is not None else {})
		if not include_reservation:
			arguments.pop("reservation_id", None)
		if not include_library:
			arguments.pop("lib", None)
		if lib is not None:
			arguments["lib"] = lib
		arguments.setdefault("lib", None)
		arguments.update(extra)
		return arguments

	def get_backend_info(self, lib=None, refresh=False):
		"""Backend metadata: whether the QPM has device metadata at all, and
		for the shim the provider's static architecture. `refresh` reads the
		device rather than the QPM's cache; only ask it of a QPM that
		publishes refreshes_backend_info."""
		extra = {"refresh": True} if refresh else {}
		return self._api.get_backend_info(
			**self._arguments(lib, include_reservation=True, **extra))

	def get_device_info(self, lib=None):
		"""The device record (qhw-device-v1 from the shim)."""
		return self._api.get_device_info(**self._arguments(lib))

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     with_reservation=False):
		return self._api.get_dynamic_backend_info(
			**self._arguments(lib, include_reservation=with_reservation,
					  include_library=False,
					  calibration_set_id=calibration_set_id))

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None):
		"""The calibration record (qhw-calibration-v1 from the shim)."""
		return self._api.get_calibration_snapshot(
			**self._arguments(lib, calibration_set_id=calibration_set_id))

	def get_coupling_graph(self, calibration_set_id=None, lib=None):
		"""The coupling record (qhw-coupling-v1 from the shim): edges and
		the native operations with their loci."""
		return self._api.get_coupling_graph(
			**self._arguments(lib, calibration_set_id=calibration_set_id))

	def metadata_supported(self, lib=None):
		"""Whether the QPM describes a device, as get_backend_info says."""
		from .qfw_target import metadata_supported
		return metadata_supported(self.get_backend_info(lib=lib))
