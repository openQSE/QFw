"""Qiskit BackendV2 adapter over an allocation-scoped libquantum context."""

from qiskit.providers import BackendV2, Options
from qiskit.providers.exceptions import QiskitBackendNotFoundError

from .qhw_target import qhw_to_qiskit_target


class QFwBackendV2(BackendV2):
	"""Managed QFw backend using libquantum for discovery and execution."""

	BACKEND_NAME = "qfw_v2"
	BACKEND_VERSION = "0.1"
	PROVIDER_ID = "qfw.libquantum"

	def __init__(self, *, route_policy=None, finalize_timeout=300):
		super().__init__(
			provider=None,
			name=self.BACKEND_NAME,
			description="Allocation-scoped QFw libquantum backend",
			backend_version=self.BACKEND_VERSION,
		)
		self._initialize_state(finalize_timeout)
		context = None
		try:
			import libquantum as lq
			from libquantum_compiler_qiskit import plugin_path

			context = lq.initialize(lq.InitConfig(
				compiler_paths=(plugin_path(),),
				enabled_compilers=frozenset({"qiskit"}),
			))
			self._attach_context(context, route_policy)
		except Exception as exc:
			if context is not None:
				_try_finalize(context, finalize_timeout)
			raise QiskitBackendNotFoundError(
				f"QFwBackendV2 initialization failed: {exc}") from exc

	@classmethod
	def _from_context(cls, context, *, route_policy=None, finalize_timeout=300):
		"""Create an isolated test backend without changing the public API."""
		backend = cls.__new__(cls)
		BackendV2.__init__(
			backend,
			provider=None,
			name=cls.BACKEND_NAME,
			description="Allocation-scoped QFw libquantum backend",
			backend_version=cls.BACKEND_VERSION,
		)
		backend._initialize_state(finalize_timeout)
		try:
			backend._attach_context(context, route_policy)
		except Exception:
			_try_finalize(context, finalize_timeout)
			raise
		return backend

	def _initialize_state(self, finalize_timeout):
		if isinstance(finalize_timeout, bool) or finalize_timeout < 0:
			raise ValueError("finalize_timeout must be nonnegative")
		self._finalize_timeout = finalize_timeout
		self._context = None
		self._route = None
		self._target = None
		self._closed = False
		self._jobs = set()

	def _attach_context(self, context, route_policy):
		definition = _select_qiskit_route(context, route_policy)
		route = context.open_route(definition)
		target = qhw_to_qiskit_target(
			route.device.qhw_device,
			route.device.qhw_coupling,
			route.device.qhw_calibration,
		)
		self._context = context
		self._route = route
		self._target = target

	@property
	def target(self):
		return self._target

	@property
	def max_circuits(self):
		return 1024

	@classmethod
	def _default_options(cls):
		return Options(shots=1024, timeout=None)

	def run(self, circuits, **run_options):
		from .qfw_job_v2 import QFwJobV2

		self._require_open()
		job = QFwJobV2(self, circuits, run_options)
		self._jobs.add(job)
		job.submit()
		return job

	def shutdown(self, timeout=None):
		if self._closed:
			return self._shutdown_result
		self._closed = True
		import libquantum as lq

		self._shutdown_result = lq.finalize(
			self._context,
			timeout=self._finalize_timeout if timeout is None else timeout,
		)
		return self._shutdown_result

	def _require_open(self):
		if self._closed:
			raise RuntimeError("QFwBackendV2 is shut down")


def _select_qiskit_route(context, route_policy):
	query = context.list_routes(explain=True)
	eligible = tuple(
		route for route in query.routes
		if route.compiler is not None
		and route.compiler.info.compiler_id == "qiskit"
		and route.compiler_input is not None
		and route.compiler_input.representation.startswith(
			"python:qiskit.circuit.quantumcircuit.")
	)
	preferences = _route_preferences(route_policy)
	if preferences:
		for compiler_id in preferences:
			matches = tuple(
				route for route in eligible
				if route.compiler.info.compiler_id == compiler_id
			)
			if matches:
				eligible = matches
				break
		else:
			eligible = ()
	if len(eligible) == 1:
		return eligible[0]
	if not eligible:
		reasons = "; ".join(
			f"{item.stage}:{item.reason}" for item in query.rejected)
		raise QiskitBackendNotFoundError(
			"no eligible Qiskit compiler route"
			+ (f": {reasons}" if reasons else "")
		)
	services = ", ".join(sorted(
		f"{route.device.device_id}/{route.compiler.info.compiler_id}"
		for route in eligible
	))
	raise QiskitBackendNotFoundError(
		f"ambiguous Qiskit compiler routes: {services}")


def _route_preferences(route_policy):
	if route_policy is None:
		return ()
	if isinstance(route_policy, str) and route_policy:
		return (route_policy,)
	if isinstance(route_policy, (tuple, list)) and route_policy:
		if all(isinstance(item, str) and item for item in route_policy):
			return tuple(route_policy)
	raise TypeError(
		"route_policy must be a compiler ID or ordered compiler IDs")


def _try_finalize(context, timeout):
	try:
		import libquantum as lq
		lq.finalize(context, timeout=timeout)
	except Exception:
		pass
