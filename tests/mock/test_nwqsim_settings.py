import json
import types

import pytest

from defw_exception import DEFwExecutionError
from svc_nwqsim_qpm.settings import nwqsim_backend
from svc_nwqsim_qpm.svc_qrc import QRC
from util import service_listing


@pytest.fixture
def command(tmp_path, monkeypatch):
	monkeypatch.setenv("QFW_QPM_SERVICE_NAME", "nwqsim")
	monkeypatch.delenv("QFW_DVM_URI_PATH", raising=False)
	monkeypatch.setattr("shutil.which", lambda name: f"/opt/bin/{name}")

	def form(circuit_info, **nwqsim):
		entry = {"name": "nwqsim", "module": "svc_nwqsim_qpm"}
		if nwqsim:
			entry["nwqsim"] = nwqsim
		path = tmp_path / "services.yaml"
		path.write_text(json.dumps({"services": [entry]}))
		monkeypatch.setenv("QFW_SERVICE_CONFIG", str(path))
		circuit = types.SimpleNamespace(info={
			"qfw_backend": "circuit_runner.nwqsim", "np": 1, **circuit_info})
		return QRC(start=False).form_cmd(circuit, "c.qasm")
	return form


@pytest.mark.parametrize("nwqsim, accelerator", [
	({}, "cpu"),
	({"backend": "CPU"}, "cpu"),
	({"backend": "OpenMP"}, "cpu"),
	({"backend": "MPI"}, "cpu"),
	({"backend": "NVGPU"}, "gpu"),
	({"backend": "nvgpu_mpi"}, "gpu"),
	({"backend": "AMDGPU"}, "gpu"),
])
def test_an_nwqsim_service_lists_the_device_its_backend_runs_on(
		nwqsim, accelerator):
	record = service_listing.service_record(
		{"name": "nwqsim", "module": "svc_nwqsim_qpm", "nwqsim": nwqsim})

	assert record["accelerator"] == accelerator


def test_a_backend_nwqsim_lacks_is_refused():
	with pytest.raises(DEFwExecutionError, match="nwqsim backend must be one of"):
		nwqsim_backend({"nwqsim": {"backend": "TPU"}})


def test_the_runner_runs_the_entrys_backend_whatever_the_circuit_asks(command):
	formed = command({"backend": "OPENMP"}, backend="NVGPU")

	assert "-backend NVGPU" in formed
	assert "OPENMP" not in formed


def test_the_runner_defaults_to_openmp(command):
	assert "-backend OPENMP" in command({})
