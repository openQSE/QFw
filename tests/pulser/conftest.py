# The Pulser client against real Pulser (setup/pulser-requirements.txt), with
# the QPM stood in for. The DEFw runtime is a C extension a test has no use
# for, so only the names the client imports are stubbed.
import pathlib
import sys
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in ("backends", "services", "service-apis", "DEFw/python/infra"):
	if str(REPO_ROOT / path) not in sys.path:
		sys.path.insert(0, str(REPO_ROOT / path))

cdefw_global = types.ModuleType("cdefw_global")
cdefw_global.get_node_name = lambda: "test"
sys.modules.setdefault("cdefw_global", cdefw_global)
defw = types.ModuleType("defw")
defw.me = None
defw.get_agent = lambda *args, **kwargs: None
sys.modules.setdefault("defw", defw)
defw_app_util = types.ModuleType("defw_app_util")
defw_app_util.SYSTEM_UP_TIMEOUT = 1
defw_app_util.defw_get_directory_service = lambda timeout=None: None
sys.modules.setdefault("defw_app_util", defw_app_util)
# The client imports helpers from the Qiskit frontend's modules, not its
# backend, which the package's __init__ would load with the DEFw runtime.
qfw_qiskit = types.ModuleType("qfw_qiskit")
qfw_qiskit.__path__ = [str(REPO_ROOT / "backends" / "qfw_qiskit")]
sys.modules.setdefault("qfw_qiskit", qfw_qiskit)
