# QPU front-end shim — bifurcation layer for svc_lib_qpm.
#
# This service is QFw's *own implementation* of the front-end contract: a
# separate, parallel QPM service (alongside the native svc_iqm_qpm, which is
# left untouched for evaluation). It implements the api_qpm surface and
# internally routes each contract call to QRMI or QDMI.
#
# Routing is driven by a PER-RESOURCE descriptor (descriptor.py; design doc
# qpu-frontend-contract.md sections 5 and 5.1), not by a fixed per-library
# table. For a call, the candidate libraries are:
#
#     descriptor.caps[call]  ∩  wired libraries  ∩  driver.implements(call)
#
# i.e. what the resource's descriptor says covers the call, restricted to the
# libraries wired for this resource, restricted to the calls the driver can
# actually serve (its static CAPABILITIES). Among the
# candidates:
#   1. execution-family calls pin to the reservation/execution owner;
#   2. otherwise, if more than one, an explicit preference breaks the tie
#      (QFW_QPU_IFACE_PREF overrides the descriptor's preference);
#   3. otherwise the first candidate.

from defw_exception import DEFwExecutionError
import logging
import os

PREFERENCE_ENV = "QFW_QPU_IFACE_PREF"

# The contract is the union of operations (today: the IQMServiceClient surface).
CONTRACT_CALLS = (
	"get_backend_info",
	"get_device_info",
	"get_dynamic_backend_info",
	"get_calibration_snapshot",
	"get_coupling_graph",
	"run_circuit",
	"get_task_timing",
	"get_task_metadata",
)

# Execution-family calls share backend state and must stay within one library
# — the reservation/execution owner — never split by per-call preference.
EXECUTION_CALLS = frozenset({
	"run_circuit",
	"get_task_timing",
	"get_task_metadata",
})


class NotImplementedByLibrary(DEFwExecutionError):
	"""No wired library implements a requested contract call for this resource
	(the NOT_IMPLEMENTED / gap-map signal)."""


class Frontend:
	def __init__(self, drivers, descriptor):
		# drivers: iterable of driver objects, each exposing `.name`,
		#   `.implements(call)`, and the contract methods it covers.
		# descriptor: the per-resource capability descriptor (descriptor.py).
		self._drivers = {}
		for d in drivers:
			self._drivers[d.name] = d
		if not self._drivers:
			raise DEFwExecutionError("Frontend requires at least one driver")

		self._id = descriptor.get("id")
		self._caps = descriptor.get("caps", {})
		# Libraries wired for this resource (default: whatever drivers exist).
		self._wired = set(descriptor.get("libraries", self._drivers.keys()))
		# Preference (composable tiebreaker): env var overrides the descriptor.
		self._preference = (os.environ.get(PREFERENCE_ENV)
				or descriptor.get("preference"))
		# Reservation/execution owner: from the descriptor, else first wired
		# library that can run a circuit.
		self._execution_owner = (descriptor.get("execution_owner")
				or self._default_exec_owner())

	def _default_exec_owner(self):
		for name in self._wired:
			d = self._drivers.get(name)
			if d and d.implements("run_circuit"):
				return name
		return None

	# --- routing -----------------------------------------------------

	def _normalize_lib(self, lib):
		if lib is None:
			return None
		lib = str(lib).strip().lower()
		if not lib or lib == "default":
			return None
		return lib

	def _candidates(self, call):
		# per-resource caps  ∩  wired libraries  ∩  calls the driver can serve
		return [n for n in self._caps.get(call, [])
				if n in self._wired
				and n in self._drivers
				and self._drivers[n].implements(call)]

	def route(self, call, lib=None):
		"""Return the driver chosen to handle `call` (the routing decision)."""
		cands = self._candidates(call)
		if not cands:
			raise NotImplementedByLibrary(
				f"no wired library implements {call!r} for "
				f"resource {self._id!r}")
		lib = self._normalize_lib(lib)
		if lib:
			if lib not in self._wired:
				raise NotImplementedByLibrary(
					f"library {lib!r} is not wired for resource "
					f"{self._id!r}")
			if lib not in self._drivers:
				raise NotImplementedByLibrary(
					f"library {lib!r} does not have a driver for resource "
					f"{self._id!r}")
			if lib not in cands:
				raise NotImplementedByLibrary(
					f"library {lib!r} does not implement {call!r} for "
					f"resource {self._id!r}")
			return self._drivers[lib]
		if call in EXECUTION_CALLS and self._execution_owner in cands:
			return self._drivers[self._execution_owner]
		if len(cands) == 1:
			return self._drivers[cands[0]]
		if self._preference in cands:
			return self._drivers[self._preference]
		# stable default: first candidate listed in the descriptor
		return self._drivers[cands[0]]

	def _dispatch(self, call, *args, lib=None, **kwargs):
		driver = self.route(call, lib=lib)
		logging.debug("shim: routing %s -> %s", call, driver.name)
		return getattr(driver, call)(*args, **kwargs)

	def capability_map(self):
		"""Per-resource gap map: which wired libraries cover each call."""
		return {call: self._candidates(call) for call in CONTRACT_CALLS}

	# --- contract surface (delegates to the routed driver) -----------

	def get_backend_info(self, lib=None):
		return self._dispatch("get_backend_info", lib=lib)

	def get_device_info(self, lib=None):
		return self._dispatch("get_device_info", lib=lib)

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None):
		return self._dispatch(
			"get_dynamic_backend_info", calibration_set_id, lib=lib)

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None):
		return self._dispatch(
			"get_calibration_snapshot", calibration_set_id, lib=lib)

	def get_coupling_graph(self, calibration_set_id=None, lib=None):
		return self._dispatch("get_coupling_graph", calibration_set_id, lib=lib)

	def run_circuit(self, circuit, lib=None):
		return self._dispatch("run_circuit", circuit, lib=lib)

	def get_task_timing(self, cid=None, lib=None):
		return self._dispatch("get_task_timing", cid, lib=lib)

	def get_task_metadata(self, cid=None, lib=None):
		return self._dispatch("get_task_metadata", cid, lib=lib)
