# The Qblox service against the real qblox-scheduler
# (setup/qblox-requirements.txt) and real Qiskit, with the DEFw runtime stood
# in for. Kept out of tests/mock deliberately: that suite installs Qiskit and
# NumPy stubs, which would shadow the real packages these tests need.
import pathlib
import sys
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in ("backends", "services", "service-apis",
             "DEFw/python/infra", "DEFw/python/service-apis"):
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
