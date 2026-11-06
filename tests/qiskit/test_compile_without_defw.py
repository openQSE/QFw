# A compile job runs on CPU nodes with no DEFw, so what it imports must load
# without defw_exception.

import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _runs_without_defw(*statements):
	script = "\n".join([
		"import sys",
		f"sys.path[:0] = [{str(REPO_ROOT / 'services')!r}, "
		f"{str(REPO_ROOT / 'backends')!r}]",
		"sys.modules['defw_exception'] = None",
		*statements,
	])
	return subprocess.run(
		[sys.executable, "-c", script], capture_output=True, text=True)


def test_the_compile_profiles_load_without_defw():
	result = _runs_without_defw(
		"import util.isa", "import util.compile_profiles",
		"import util.iqm_transcode", "import util.ibm_transcode")

	assert result.returncode == 0, result.stderr


def test_a_compiled_circuit_can_be_marked_without_defw():
	result = _runs_without_defw(
		"import qfw_qiskit.qfw_target", "from qfw_qiskit import mark_isa")

	assert result.returncode == 0, result.stderr
