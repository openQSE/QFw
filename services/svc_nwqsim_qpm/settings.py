import shutil
import subprocess

from defw_exception import DEFwExecutionError

EXECUTABLE = "circuit_runner.nwqsim"

BACKEND_ACCELERATORS = {
	"CPU": "cpu", "OPENMP": "cpu", "MPI": "cpu",
	"NVGPU": "gpu", "NVGPU_MPI": "gpu", "AMDGPU": "gpu",
}
DEFAULT_BACKEND = "OPENMP"


def nwqsim_backend(entry):
	block = dict(entry.get("nwqsim") or {})
	unknown = set(block) - {"backend"}
	if unknown:
		raise DEFwExecutionError(
			f"unknown nwqsim settings: {', '.join(sorted(unknown))}")
	backend = str(block.get("backend", DEFAULT_BACKEND)).upper()
	if backend not in BACKEND_ACCELERATORS:
		raise DEFwExecutionError(
			f"nwqsim backend must be one of "
			f"{', '.join(BACKEND_ACCELERATORS)}, not {backend!r}")
	return backend


def compiled_backends(executable):
	listing = subprocess.run(
		[executable, "-backend_list"], capture_output=True, text=True,
		check=True, timeout=60).stdout
	return [line[2:].strip().upper() for line in listing.splitlines()
		if line.startswith("- ")]


def check_backend_is_compiled(backend):
	executable = shutil.which(EXECUTABLE)
	if not executable:
		raise DEFwExecutionError(f"Couldn't find {EXECUTABLE}. Check paths")
	compiled = compiled_backends(executable)
	if backend not in compiled:
		raise DEFwExecutionError(
			f"nwqsim backend {backend} is not built into {executable}, "
			f"which offers {', '.join(compiled)}")
