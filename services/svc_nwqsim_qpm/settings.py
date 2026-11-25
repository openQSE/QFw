from defw_exception import DEFwExecutionError

DEFAULT_BACKEND = "OPENMP"
BACKEND_ACCELERATORS = {
	"CPU": "cpu", "OPENMP": "cpu", "MPI": "cpu",
	"NVGPU": "gpu", "NVGPU_MPI": "gpu", "AMDGPU": "gpu",
}


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
