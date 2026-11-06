# A compile job runs on CPU nodes with no DEFw, so what it imports must load
# without defw_exception.

import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _imports_without_defw(*modules):
	script = "\n".join([
		"import sys",
		f"sys.path[:0] = [{str(REPO_ROOT / 'services')!r}, "
		f"{str(REPO_ROOT / 'backends')!r}]",
		"sys.modules['defw_exception'] = None",
		*(f"import {module}" for module in modules),
	])
	return subprocess.run(
		[sys.executable, "-c", script], capture_output=True, text=True)


def test_the_compile_profiles_load_without_defw():
	result = _imports_without_defw(
		"util.isa", "util.compile_profiles", "util.iqm_transcode",
		"util.ibm_transcode")

	assert result.returncode == 0, result.stderr
