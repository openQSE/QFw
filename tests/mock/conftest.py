import pathlib
import sys
import types
import logging


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _add_repo_paths():
	paths = [
		REPO_ROOT / "backends",
		REPO_ROOT / "service-apis",
		REPO_ROOT / "DEFw" / "python" / "infra",
	]
	for path in paths:
		path_str = str(path)
		if path_str not in sys.path:
			sys.path.insert(0, path_str)


def _install_qfw_package_stub():
	if "qfw_qiskit" in sys.modules:
		return

	package = types.ModuleType("qfw_qiskit")
	package.__path__ = [str(REPO_ROOT / "backends" / "qfw_qiskit")]
	sys.modules["qfw_qiskit"] = package


def _install_numpy_stub():
	if "numpy" in sys.modules:
		return

	numpy = types.ModuleType("numpy")
	numpy.ndarray = list
	numpy.uint8 = int

	def zeros(shape, dtype=None):
		return {"shape": shape, "dtype": dtype}

	def array(value):
		return value

	def reshape(value, shape):
		return {"value": value, "shape": shape}

	def ravel(value):
		class _RavelResult:
			def __init__(self, inner):
				self._inner = inner

			def tolist(self):
				return list(self._inner)

		return _RavelResult(value)

	numpy.zeros = zeros
	numpy.array = array
	numpy.reshape = reshape
	numpy.ravel = ravel
	sys.modules["numpy"] = numpy

	numpy_typing = types.ModuleType("numpy.typing")
	numpy_typing.NDArray = list
	sys.modules["numpy.typing"] = numpy_typing


def _install_yaml_stub():
	if "yaml" in sys.modules:
		return

	yaml = types.ModuleType("yaml")

	class Dumper:
		pass

	def dump(data, **kwargs):
		return repr(data)

	yaml.Dumper = Dumper
	yaml.dump = dump
	sys.modules["yaml"] = yaml


def _install_defw_stubs():
	if "defw_exception" not in sys.modules:
		defw_exception = types.ModuleType("defw_exception")

		class DEFwError(Exception):
			pass

		class DEFwNotReady(DEFwError):
			pass

		class DEFwInProgress(DEFwError):
			pass

		class DEFwNotFound(DEFwError):
			pass

		class DEFwAgentNotFound(DEFwError):
			pass

		class DEFwDumper:
			pass

		defw_exception.DEFwError = DEFwError
		defw_exception.DEFwNotReady = DEFwNotReady
		defw_exception.DEFwInProgress = DEFwInProgress
		defw_exception.DEFwNotFound = DEFwNotFound
		defw_exception.DEFwAgentNotFound = DEFwAgentNotFound
		defw_exception.DEFwDumper = DEFwDumper
		sys.modules["defw_exception"] = defw_exception

	if "defw_remote" not in sys.modules:
		defw_remote = types.ModuleType("defw_remote")

		class BaseRemote:
			def __init__(self, *args, **kwargs):
				self.args = args
				self.kwargs = kwargs

		defw_remote.BaseRemote = BaseRemote
		sys.modules["defw_remote"] = defw_remote

	if "defw_app_util" not in sys.modules:
		defw_app_util = types.ModuleType("defw_app_util")

		def defw_get_resource_mgr():
			raise AssertionError("defw_get_resource_mgr must be patched in tests")

		def defw_reserve_service_by_name(*args, **kwargs):
			raise AssertionError("defw_reserve_service_by_name must be patched in tests")

		defw_app_util.defw_get_resource_mgr = defw_get_resource_mgr
		defw_app_util.defw_reserve_service_by_name = defw_reserve_service_by_name
		sys.modules["defw_app_util"] = defw_app_util

	if "defw_event_baseapi" not in sys.modules:
		defw_event_baseapi = types.ModuleType("defw_event_baseapi")

		class BaseEventAPI:
			def __init__(self, *args, **kwargs):
				self._events = []
				self._registered = False
				self._class_id = "stub-class-id"

			def register_external(self):
				self._registered = True

			def class_id(self):
				return self._class_id

			def fileno(self):
				return 0

			def get(self):
				events = list(self._events)
				self._events.clear()
				return events

		defw_event_baseapi.BaseEventAPI = BaseEventAPI
		sys.modules["defw_event_baseapi"] = defw_event_baseapi

	if "defw_common_def" not in sys.modules:
		defw_common_def = types.ModuleType("defw_common_def")

		class _RpcMetrics:
			def dump(self):
				return None

		defw_common_def.g_rpc_metrics = _RpcMetrics()
		sys.modules["defw_common_def"] = defw_common_def

	if "defw" not in sys.modules:
		defw = types.ModuleType("defw")

		class _Runtime:
			def __init__(self):
				self.endpoint = "stub-endpoint"
				self.exit_called = False

			def my_endpoint(self):
				return self.endpoint

			def exit(self):
				self.exit_called = True

		defw.me = _Runtime()
		sys.modules["defw"] = defw


def _install_qiskit_stubs():
	if "qiskit" in sys.modules:
		return

	qiskit = types.ModuleType("qiskit")

	class QuantumCircuit:
		def __init__(self, num_qubits=0, name=None):
			self.num_qubits = num_qubits
			self.name = name or "circuit"
			self.num_clbits = num_qubits
			self.cregs = []
			self.metadata = {}

	class ClassicalRegister:
		def __init__(self, size=0, name="c"):
			self.size = size
			self.name = name

	class QuantumRegister:
		def __init__(self, size=0, name="q"):
			self.size = size
			self.name = name

	class QiskitError(Exception):
		pass

	qiskit.QuantumCircuit = QuantumCircuit
	qiskit.ClassicalRegister = ClassicalRegister
	qiskit.QuantumRegister = QuantumRegister

	qasm2 = types.ModuleType("qiskit.qasm2")
	qasm2.dumps = lambda circuit: f"OPENQASM 2.0; // {getattr(circuit, 'name', 'circuit')}"
	qiskit.qasm2 = qasm2
	sys.modules["qiskit.qasm2"] = qasm2

	providers = types.ModuleType("qiskit.providers")

	class Options:
		def __init__(self, **kwargs):
			self._validators = {}
			for key, value in kwargs.items():
				setattr(self, key, value)

		def set_validator(self, name, validator):
			self._validators[name] = validator

	class BackendV2:
		def __init__(self, name=None):
			self.name = name
			self.options = type(self)._default_options()

		@classmethod
		def _default_options(cls):
			return Options()

	class JobV1:
		def __init__(self, backend, job_id):
			self._backend = backend
			self._job_id = job_id

	providers.Options = Options
	providers.BackendV2 = BackendV2
	providers.JobV1 = JobV1
	qiskit.providers = providers
	sys.modules["qiskit.providers"] = providers

	backend_mod = types.ModuleType("qiskit.providers.backend")
	backend_mod.BackendV2 = BackendV2
	sys.modules["qiskit.providers.backend"] = backend_mod

	jobstatus = types.ModuleType("qiskit.providers.jobstatus")
	JobStatus = type(
		"JobStatus",
		(),
		{
			"RUNNING": "RUNNING",
			"ERROR": "ERROR",
		},
	)
	jobstatus.JobStatus = JobStatus
	sys.modules["qiskit.providers.jobstatus"] = jobstatus

	result_mod = types.ModuleType("qiskit.result")

	class Result:
		def __init__(self, data):
			self.data = data

		@classmethod
		def from_dict(cls, data):
			return cls(data)

		def get_counts(self, circuit=None):
			results = self.data.get("results", [])
			if not results:
				return {}
			counts = [item["data"]["counts"] for item in results]
			return counts[0] if len(counts) == 1 else counts

	class Counts(dict):
		pass

	result_mod.Result = Result
	result_mod.Counts = Counts
	qiskit.result = result_mod
	sys.modules["qiskit.result"] = result_mod

	transpiler = types.ModuleType("qiskit.transpiler")

	class Target:
		def __init__(self, description=None):
			self.description = description

	class PassManager:
		def __init__(self, passes=None):
			self.passes = passes or []

	class PassManagerConfig:
		@classmethod
		def from_backend(cls, backend):
			return type("Config", (), {"basis_gates": []})()

	transpiler.Target = Target
	transpiler.PassManager = PassManager
	transpiler.PassManagerConfig = PassManagerConfig
	qiskit.transpiler = transpiler
	sys.modules["qiskit.transpiler"] = transpiler

	passes_mod = types.ModuleType("qiskit.transpiler.passes")

	class Optimize1qGatesDecomposition:
		def __init__(self, *args, **kwargs):
			self.args = args
			self.kwargs = kwargs

	passes_mod.Optimize1qGatesDecomposition = Optimize1qGatesDecomposition
	sys.modules["qiskit.transpiler.passes"] = passes_mod

	circuit_mod = types.ModuleType("qiskit.circuit")
	circuit_mod.QuantumCircuit = QuantumCircuit
	circuit_mod.ClassicalRegister = ClassicalRegister
	circuit_mod.QuantumRegister = QuantumRegister
	sys.modules["qiskit.circuit"] = circuit_mod

	exceptions_mod = types.ModuleType("qiskit.exceptions")
	exceptions_mod.QiskitError = QiskitError
	sys.modules["qiskit.exceptions"] = exceptions_mod

	quantum_info = types.ModuleType("qiskit.quantum_info")
	quantum_info.Pauli = type("Pauli", (), {})
	quantum_info.PauliList = type("PauliList", (), {})
	sys.modules["qiskit.quantum_info"] = quantum_info

	primitives_base = types.ModuleType("qiskit.primitives.base")
	primitives_base.BaseEstimatorV2 = type("BaseEstimatorV2", (), {})
	primitives_base.BaseSamplerV2 = type("BaseSamplerV2", (), {})

	primitives = types.ModuleType("qiskit.primitives")
	primitives.base = primitives_base
	sys.modules["qiskit.primitives"] = primitives
	sys.modules["qiskit.primitives.base"] = primitives_base

	class _GenericStub:
		def __init__(self, *args, **kwargs):
			self.args = args
			self.kwargs = kwargs

		@classmethod
		def __class_getitem__(cls, item):
			return cls

	primitives_containers = types.ModuleType("qiskit.primitives.containers")
	primitives_containers.DataBin = _GenericStub
	primitives_containers.EstimatorPubLike = object
	primitives_containers.PrimitiveResult = _GenericStub
	primitives_containers.PubResult = _GenericStub
	primitives_containers.BitArray = _GenericStub
	primitives_containers.SamplerPubLike = object
	primitives_containers.SamplerPubResult = _GenericStub
	primitives.containers = primitives_containers
	sys.modules["qiskit.primitives.containers"] = primitives_containers

	bindings_array = types.ModuleType("qiskit.primitives.containers.bindings_array")
	bindings_array.BindingsArray = _GenericStub
	primitives_containers.bindings_array = bindings_array
	sys.modules["qiskit.primitives.containers.bindings_array"] = bindings_array

	estimator_pub = types.ModuleType("qiskit.primitives.containers.estimator_pub")
	estimator_pub.EstimatorPub = type(
		"EstimatorPub",
		(),
		{"coerce": staticmethod(lambda pub, precision: pub)},
	)
	primitives_containers.estimator_pub = estimator_pub
	sys.modules["qiskit.primitives.containers.estimator_pub"] = estimator_pub

	sampler_pub = types.ModuleType("qiskit.primitives.containers.sampler_pub")
	sampler_pub.SamplerPub = type(
		"SamplerPub",
		(),
		{"coerce": staticmethod(lambda pub, shots: pub)},
	)
	primitives_containers.sampler_pub = sampler_pub
	sys.modules["qiskit.primitives.containers.sampler_pub"] = sampler_pub

	bit_array = types.ModuleType("qiskit.primitives.containers.bit_array")
	bit_array._min_num_bytes = lambda num_bits: max(1, num_bits)
	primitives_containers.bit_array = bit_array
	sys.modules["qiskit.primitives.containers.bit_array"] = bit_array

	primitive_job = types.ModuleType("qiskit.primitives.primitive_job")
	primitive_job.PrimitiveJob = type(
		"PrimitiveJob",
		(_GenericStub,),
		{"_submit": lambda self: None},
	)
	primitives.primitive_job = primitive_job
	sys.modules["qiskit.primitives.primitive_job"] = primitive_job

	sys.modules["qiskit"] = qiskit

	qiskit_aer = types.ModuleType("qiskit_aer")
	backends = types.ModuleType("qiskit_aer.backends")
	name_mapping = types.ModuleType("qiskit_aer.backends.name_mapping")
	name_mapping.NAME_MAPPING = {}
	qiskit_aer.backends = backends
	sys.modules["qiskit_aer"] = qiskit_aer
	sys.modules["qiskit_aer.backends"] = backends
	sys.modules["qiskit_aer.backends.name_mapping"] = name_mapping


_add_repo_paths()
_install_qfw_package_stub()
_install_numpy_stub()
_install_yaml_stub()
_install_defw_stubs()
_install_qiskit_stubs()

if not hasattr(logging, "defw_app"):
	logging.defw_app = lambda *args, **kwargs: None
