# Share service imports and DEFw exception stubs across Qiskit tests.
# Install before collection so exception identities do not depend on test order.

import pathlib
import sys
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

if "defw_exception" not in sys.modules:
	try:
		import defw_exception  # noqa: F401
	except ImportError:
		class _StubError(Exception):
			pass

		class _DEFwExceptionStub(types.ModuleType):
			def __getattr__(self, name):
				if not name.startswith("DEFw"):
					raise AttributeError(name)
				minted = type(name, (_StubError,), {})
				setattr(self, name, minted)
				return minted

		sys.modules["defw_exception"] = _DEFwExceptionStub("defw_exception")
