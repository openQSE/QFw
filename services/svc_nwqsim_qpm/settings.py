from defw_exception import DEFwExecutionError

BACKEND_ACCELERATORS = {
	"CPU": "cpu", "OPENMP": "cpu", "MPI": "cpu",
	"NVGPU": "gpu", "NVGPU_MPI": "gpu", "AMDGPU": "gpu",
}


def nwqsim_backend(entry):
	backend = str((entry.get("nwqsim") or {}).get("backend", "OPENMP")).upper()
	if backend not in BACKEND_ACCELERATORS:
		raise DEFwExecutionError(
			f"nwqsim backend must be one of "
			f"{', '.join(BACKEND_ACCELERATORS)}, not {backend!r}")
	return backend
