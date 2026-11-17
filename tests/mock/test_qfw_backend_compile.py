# QFwBackend.compile(): the compiler it uses comes from its argument, else
# QFW_COMPILER, else Qiskit, so a batch script can switch a program that does
# not name one. Both compilers are stand-ins here; what is checked is which one
# is called, and that it gets the backend's target and the caller's options.
import sys
import types

import pytest

from tests.mock.fakes import FakeEventAPI, FakeQPM, FakeRuntime


class FakeCompiler:
	def __init__(self, name):
		self.name = name
		self.calls = []

	def __call__(self, circuits, **kwargs):
		self.calls.append((circuits, kwargs))
		return f"{self.name}-compiled"


@pytest.fixture
def compilers(monkeypatch):
	# Replace qiskit.transpile and MQT Core's mlir.compile_program. The mock
	# suite's qiskit stand-in has no transpile, hence raising=False. The mqt
	# packages are replaced whole, so the test neither needs MQT Core nor
	# picks up a real mlir module an earlier import left behind.
	import qiskit

	transpile = FakeCompiler("qiskit")
	compile_program = FakeCompiler("mqt")
	monkeypatch.setattr(qiskit, "transpile", transpile, raising=False)
	mlir = types.ModuleType("mqt.core.mlir")
	mlir.compile_program = compile_program
	core = types.ModuleType("mqt.core")
	core.mlir = mlir
	mqt = types.ModuleType("mqt")
	mqt.__path__ = []
	mqt.core = core
	monkeypatch.setitem(sys.modules, "mqt", mqt)
	monkeypatch.setitem(sys.modules, "mqt.core", core)
	monkeypatch.setitem(sys.modules, "mqt.core.mlir", mlir)
	return {"qiskit": transpile, "mqt": compile_program}


@pytest.fixture
def backend(monkeypatch):
	import qfw_qiskit.qfw_simulator as qfw_simulator

	monkeypatch.setattr(
		qfw_simulator, "get_qpm",
		lambda *args, **kwargs: (FakeQPM(), None))
	monkeypatch.setattr(
		qfw_simulator, "BaseEventAPI",
		lambda: FakeEventAPI(class_id="event-api-compile"))
	monkeypatch.setattr(
		qfw_simulator, "me", FakeRuntime(endpoint="endpoint-compile"))
	monkeypatch.delenv(qfw_simulator.COMPILER_ENV, raising=False)
	return qfw_simulator.QFwBackend()


def _called(compilers):
	return [name for name, fake in compilers.items() if fake.calls]


def test_compile_uses_qiskit_by_default(backend, compilers):
	assert backend.compile("circuit", optimization_level=2) == \
		"qiskit-compiled"

	assert compilers["qiskit"].calls == [(
		"circuit",
		{"target": backend.target, "optimization_level": 2},
	)]
	assert _called(compilers) == ["qiskit"]


def test_qfw_compiler_picks_mqt(backend, compilers, monkeypatch):
	monkeypatch.setenv("QFW_COMPILER", "mqt")

	assert backend.compile("circuit", seed=7) == "mqt-compiled"

	assert compilers["mqt"].calls == [(
		"circuit",
		{"target": backend.target, "seed": 7},
	)]
	assert _called(compilers) == ["mqt"]


@pytest.mark.parametrize("env, argument", [
	("mqt", "qiskit"),
	("qiskit", "mqt"),
	("tket", "mqt"),
])
def test_compiler_argument_wins_over_qfw_compiler(
		backend, compilers, monkeypatch, env, argument):
	monkeypatch.setenv("QFW_COMPILER", env)

	backend.compile("circuit", compiler=argument)

	assert _called(compilers) == [argument]


@pytest.mark.parametrize("value, expected", [
	(" MQT ", "mqt"),
	("Qiskit", "qiskit"),
	("", "qiskit"),
	("   ", "qiskit"),
])
def test_qfw_compiler_ignores_case_and_blank(
		backend, compilers, monkeypatch, value, expected):
	monkeypatch.setenv("QFW_COMPILER", value)

	backend.compile("circuit")

	assert _called(compilers) == [expected]


def test_unknown_qfw_compiler_names_the_variable(
		backend, compilers, monkeypatch):
	monkeypatch.setenv("QFW_COMPILER", "tket")

	with pytest.raises(ValueError, match=(
			r"unknown compiler 'tket' \(from QFW_COMPILER\); "
			r"expected 'qiskit' or 'mqt'")):
		backend.compile("circuit")

	assert _called(compilers) == []


def test_unknown_compiler_argument(backend, compilers):
	with pytest.raises(ValueError, match=(
			r"^unknown compiler 'staq'; expected 'qiskit' or 'mqt'$")):
		backend.compile("circuit", "staq")

	assert _called(compilers) == []
