import json
import types

import pytest

from api_qpm_common import QPMType
from defw_exception import DEFwError, DEFwExecutionError
from qfw_qiskit.qpm_selection import qpm_selection_for_provider
import svc_aer_qpm.svc_qpm as svc_qpm
from svc_aer_qpm.settings import aer_settings_from_service_entry
from svc_aer_qpm.svc_qpm import QPM
from svc_aer_qpm.svc_qrc import QRC

CONFIGURATION = {
	"backend_name": "tiny", "n_qubits": 2, "basis_gates": ["cz", "sx", "rz"],
	"coupling_map": [[0, 1]], "gates": []}
PROPERTIES = {"backend_name": "tiny", "qubits": [], "gates": []}


@pytest.fixture
def service(tmp_path, monkeypatch):
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")
	monkeypatch.setenv("QFW_QPM_SERVICE_NAME", "aer")
	monkeypatch.delenv("QFW_QPU_DEVICE_ID", raising=False)
	monkeypatch.setattr(svc_qpm, "available_devices", lambda: ("CPU",))

	def configure(device_file=False, mpi_launch=None, **aer):
		entry = {"name": "aer", "module": "svc_aer_qpm"}
		if aer:
			entry["aer"] = aer
		path = tmp_path / "services.yaml"
		path.write_text(json.dumps(
			{"mpi-launch": mpi_launch or {}, "services": [entry]}))
		monkeypatch.setenv("QFW_SERVICE_CONFIG", str(path))
		if device_file:
			records = tmp_path / "tiny.json"
			records.write_text(json.dumps(
				{"configuration": CONFIGURATION, "properties": PROPERTIES}))
			monkeypatch.setenv("QFW_QPU_DEVICE_ID", str(records))
	return configure


def test_selection_types_the_provider_as_a_simulator():
	selection = qpm_selection_for_provider("aer")

	assert selection["qpm_type"] == QPMType.QPM_TYPE_SIMULATOR
	assert selection["provider"] == "aer"


def test_settings_default_to_ideal_on_the_cpu():
	settings = aer_settings_from_service_entry({})

	assert (settings.mode, settings.device, settings.method) == (
		"ideal", "CPU", "automatic")


@pytest.mark.parametrize("aer, refusal", [
	({"mode": "exact"}, "aer mode"),
	({"device": "TPU"}, "aer device"),
	({"target-gpus": [0]}, "target-gpus"),
	({"shots": 10}, "unknown aer settings"),
	({"mps-max-bond-dimension": 64}, "needs method: matrix_product_state"),
	({"method": "statevector", "mps-truncation-threshold": 1e-10},
	 "needs method: matrix_product_state"),
	({"mps-log-data": True}, "needs method: matrix_product_state"),
])
def test_settings_refuse_what_aer_cannot_run(aer, refusal):
	with pytest.raises(DEFwExecutionError, match=refusal):
		aer_settings_from_service_entry({"aer": aer})


def test_noisy_mode_needs_a_device_to_emulate(service):
	service(mode="noisy")

	with pytest.raises(DEFwExecutionError, match="device-id"):
		QPM(start=False)


def test_ideal_mode_without_a_device_needs_a_width(service):
	service(mode="ideal")

	with pytest.raises(DEFwExecutionError, match="num-qubits"):
		QPM(start=False)


def test_a_gpu_the_host_lacks_is_refused_rather_than_run_on_the_cpu(service):
	service(mode="ideal", device="GPU", **{"num-qubits": 20})

	with pytest.raises(DEFwExecutionError, match="offers CPU"):
		QPM(start=False)


def test_a_gpu_the_host_has_is_used(service, monkeypatch):
	monkeypatch.setattr(svc_qpm, "available_devices", lambda: ("CPU", "GPU"))
	service(mode="ideal", device="GPU", **{"num-qubits": 20})

	assert QPM(start=False).settings.device == "GPU"


def test_the_settings_are_published(service, monkeypatch):
	service(device_file=True, mode="noisy", method="statevector")
	published = {}
	qpm = QPM(start=False)
	monkeypatch.setattr(
		qpm, "query_helper",
		lambda *args, properties: published.update(properties))

	qpm.query()

	assert published["provider"] == "aer"
	assert (published["aer_mode"], published["aer_device"],
		published["aer_method"]) == ("noisy", "CPU", "statevector")
	assert published["num_qubits"] == 2


MPS_SETTINGS = {
	"method": "matrix_product_state", "mps-max-bond-dimension": 64,
	"mps-truncation-threshold": 1e-10, "mps-log-data": True}


def test_the_mps_settings_are_published(service, monkeypatch):
	service(device_file=True, mode="noisy", **MPS_SETTINGS)
	published = {}
	qpm = QPM(start=False)
	monkeypatch.setattr(
		qpm, "query_helper",
		lambda *args, properties: published.update(properties))

	qpm.query()

	assert published["aer_mps_max_bond_dimension"] == 64
	assert published["aer_mps_truncation_threshold"] == 1e-10
	assert published["aer_mps_log_data"] is True


def test_telemetry_publishes_the_device_records(service):
	service(device_file=True, mode="noisy")

	info = QPM(start=False).get_backend_info()

	assert info["metadata_supported"] is True
	assert info["static_architecture"] == CONFIGURATION
	assert info["backend_properties"] == PROPERTIES


def test_ideal_mode_without_a_device_describes_none(service):
	service(mode="ideal", **{"num-qubits": 30})

	qpm = QPM(start=False)

	assert qpm.num_qubits == 30
	assert qpm.get_backend_info()["metadata_supported"] is False


def test_services_emulating_one_device_have_their_own_backend_names(
		service, monkeypatch):
	service(device_file=True, mode="noisy")
	names = set()
	for service_id in ("tiny-a", "tiny-b"):
		monkeypatch.setenv("QFW_QPM_SERVICE_ID", service_id)
		names.add(QPM(start=False).get_backend_info()["backend"])

	assert names == {"tiny-a", "tiny-b"}


def _circuit(service, monkeypatch, seed=None, mpi_launch=None, **aer):
	monkeypatch.setattr(svc_qpm, "available_devices", lambda: ("CPU", "GPU"))
	service(device_file=True, mpi_launch=mpi_launch, **aer)
	info = QPM(start=False).prepare_circuit(
		{"num_shots": 100, "np": 1, "seed_simulator": seed})
	return types.SimpleNamespace(info=info)


def test_the_runner_gets_the_settings(service, monkeypatch):
	circuit = _circuit(
		service, monkeypatch, mode="noisy", device="GPU",
		method="matrix_product_state", **{"target-gpus": [0, 1]})

	command = QRC(start=False).form_cmd(circuit, "c.qasm")

	assert "--mode noisy" in command
	assert "--method matrix_product_state" in command
	assert "--device GPU" in command
	assert "--target-gpus 0 1" in command
	assert "--device-id" in command


def test_the_runner_gets_the_mps_settings(service, monkeypatch):
	command = QRC(start=False).form_cmd(
		_circuit(service, monkeypatch, mode="noisy", **MPS_SETTINGS), "c.qasm")

	assert "--mps-max-bond-dimension 64" in command
	assert "--mps-truncation-threshold 1e-10" in command
	assert "--mps-log-data" in command


def test_the_runner_leaves_the_mps_settings_to_aer_by_default(
		service, monkeypatch):
	command = QRC(start=False).form_cmd(
		_circuit(service, monkeypatch, method="matrix_product_state"), "c.qasm")

	assert "--mps-" not in command


def test_the_runner_runs_on_one_rank_whatever_the_circuit_asks(
		service, monkeypatch):
	circuit = _circuit(service, monkeypatch)
	circuit.info["np"] = 4

	command = QRC(start=False).form_cmd(circuit, "c.qasm")

	assert "--np 1 " in command


def test_the_runner_is_unbound_and_gets_the_thread_count(
		service, monkeypatch):
	monkeypatch.setenv("OMP_NUM_THREADS", "64")

	command = QRC(start=False).form_cmd(
		_circuit(service, monkeypatch), "c.qasm")

	assert "--bind-to none" in command
	assert "-x OMP_NUM_THREADS" in command


def test_the_sites_binding_and_exports_are_kept(service, monkeypatch):
	monkeypatch.setenv("OMP_NUM_THREADS", "64")
	mpi_launch = {"bind-to": "socket", "export-env": ["OMP_NUM_THREADS"]}

	command = QRC(start=False).form_cmd(
		_circuit(service, monkeypatch, mpi_launch=mpi_launch), "c.qasm")

	assert "--bind-to none" not in command
	assert "--bind-to socket" in command
	assert command.count("-x OMP_NUM_THREADS") == 1


def test_the_runner_gets_the_job_seed(service, monkeypatch):
	command = QRC(start=False).form_cmd(
		_circuit(service, monkeypatch, seed=7), "c.qasm")

	assert "--seed 7" in command


def test_the_runner_is_unseeded_without_a_job_seed(service, monkeypatch):
	command = QRC(start=False).form_cmd(
		_circuit(service, monkeypatch), "c.qasm")

	assert "--seed" not in command


def test_the_runner_counts_are_the_result():
	qrc = QRC(start=False)

	assert qrc.parse_result(
		b'warning\n{"counts": {"00": 60, "11": 40}, "diagnostics": {"method": "mps"}}\n'
	) == {"counts": {"00": 60, "11": 40}, "diagnostics": {"method": "mps"}}
	with pytest.raises(DEFwError):
		qrc.parse_result(b"Traceback")
