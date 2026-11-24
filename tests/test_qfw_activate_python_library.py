import ctypes.util
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DISTRIBUTION_PYTHON = Path("/usr/bin/python3")
PYTHON_LIBRARY_DIRS = re.compile(r"^_qfw_python_lib_dirs=\((.*)\)$", re.MULTILINE)
PYTHON_LIBRARY_DIR = sysconfig.get_config_var("LIBDIR") or "/usr/lib"

needs_bundled_defw_configure = pytest.mark.skipif(
	not (shutil.which("cmake") and shutil.which("swig")
		and ctypes.util.find_library("uuid")),
	reason="configuring the bundled DEFw needs cmake, swig and libuuid")


def _activate_python_library_dirs(build_dir, python):
	subprocess.run(
		["cmake", "-S", str(REPO_ROOT), "-B", str(build_dir),
		 "-DQFW_BUILD_BUNDLED_DEFW=ON", f"-DPython3_EXECUTABLE={python}"],
		check=True, capture_output=True, text=True)
	activate = (build_dir / "generated" / "bin" / "qfw-activate").read_text()
	(listed,) = PYTHON_LIBRARY_DIRS.findall(activate)
	return {os.path.realpath(path) for path in re.findall(r'"([^"]+)"', listed)}


@needs_bundled_defw_configure
@pytest.mark.skipif(
	PYTHON_LIBRARY_DIR.startswith(("/usr/", "/lib")),
	reason="this interpreter's library is in a system directory")
def test_activate_puts_a_relocated_python_library_on_the_loader_path(tmp_path):
	dirs = _activate_python_library_dirs(tmp_path, sys.executable)

	assert os.path.realpath(PYTHON_LIBRARY_DIR) in dirs


@needs_bundled_defw_configure
@pytest.mark.skipif(
	not DISTRIBUTION_PYTHON.exists(), reason="no distribution Python at /usr/bin/python3")
def test_activate_adds_nothing_for_a_distribution_python(tmp_path):
	assert _activate_python_library_dirs(tmp_path, DISTRIBUTION_PYTHON) == set()
