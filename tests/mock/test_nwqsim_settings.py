import json
import types

import pytest

from defw_exception import DEFwExecutionError
import svc_nwqsim_qpm.svc_qrc as svc_qrc
from svc_nwqsim_qpm.settings import check_backend_is_compiled, nwqsim_backend
from util import service_listing
from util.qpm.util_qrc import UTIL_QRC

BUILT_FOR_THE_CPU = "Available backends:\n- CPU\n- OpenMP\n"


@pytest.fixture
def service(tmp_path, monkeypatch):
	monkeypatch.setenv("QFW_QPM_SERVICE_NAME", "nwqsim")
	monkeypatch.delenv("QFW_DVM_URI_PATH", raising=False)

	def configure(**nwqsim):
		entry = {"name": "nwqsim", "module": "svc_nwqsim_qpm"}
		if nwqsim:
			entry["nwqsim"] = nwqsim
		path = tmp_path / "services.yaml"
		path.write_text(json.dumps({"services": [entry]}))
		monkeypatch.setenv("QFW_SERVICE_CONFIG", str(path))
	return configure


@pytest.fixture
def nwqsim_build(tmp_path, monkeypatch):
	def install(backend_list):
		executable = tmp_path / "bin" / "circuit_runner.nwqsim"
		executable.parent.mkdir(exist_ok=True)
		executable.write_text(f"#!/bin/sh\nprintf '{backend_list}'\n")
		executable.chmod(0o755)
		monkeypatch.setenv("PATH", str(executable.parent))
	return install


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


@pytest.mark.parametrize("nwqsim, refusal", [
	({"backend": "TPU"}, "nwqsim backend must be one of"),
	({"shots": 10}, "unknown nwqsim settings: shots"),
])
def test_settings_refuse_what_nwqsim_cannot_run(nwqsim, refusal):
	with pytest.raises(DEFwExecutionError, match=refusal):
		nwqsim_backend({"nwqsim": nwqsim})


def test_the_runner_runs_the_entrys_backend_whatever_the_circuit_asks(
		service, monkeypatch):
	service(backend="NVGPU")
	monkeypatch.setattr("shutil.which", lambda name: f"/opt/bin/{name}")
	circuit = types.SimpleNamespace(info={
		"qfw_backend": "circuit_runner.nwqsim", "np": 1, "num_shots": 10,
		"backend": "OPENMP"})

	command = svc_qrc.QRC(start=False).form_cmd(circuit, "c.qasm")

	assert "-backend NVGPU" in command
	assert "OPENMP" not in command


def test_the_runner_defaults_to_openmp(service, monkeypatch):
	service()
	monkeypatch.setattr("shutil.which", lambda name: f"/opt/bin/{name}")
	circuit = types.SimpleNamespace(info={
		"qfw_backend": "circuit_runner.nwqsim", "np": 1})

	assert "-backend OPENMP" in svc_qrc.QRC(start=False).form_cmd(
		circuit, "c.qasm")


def test_a_backend_the_build_has_is_accepted(nwqsim_build):
	nwqsim_build(BUILT_FOR_THE_CPU)

	check_backend_is_compiled("OPENMP")


def test_a_backend_the_build_lacks_is_refused(nwqsim_build):
	nwqsim_build(BUILT_FOR_THE_CPU)

	with pytest.raises(DEFwExecutionError, match="NVGPU is not built into"):
		check_backend_is_compiled("NVGPU")


def test_a_missing_executable_is_refused(tmp_path, monkeypatch):
	monkeypatch.setenv("PATH", str(tmp_path))

	with pytest.raises(DEFwExecutionError, match="circuit_runner.nwqsim"):
		check_backend_is_compiled("OPENMP")


def test_a_started_runner_checks_its_backend_against_the_build(
		service, monkeypatch):
	service(backend="NVGPU")
	checked = []
	monkeypatch.setattr(svc_qrc, "check_backend_is_compiled", checked.append)
	monkeypatch.setattr(UTIL_QRC, "__init__", lambda self, start=True: None)

	svc_qrc.QRC(start=True)

	assert checked == ["NVGPU"]
