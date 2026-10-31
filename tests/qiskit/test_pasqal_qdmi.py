# QFw's Pasqal profile with released Core, Pulser and native device wheels.

import json
import math
import pathlib
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("pasqal.qdmi")
pulser = pytest.importorskip("pulser")
from pulser.devices import MockDevice  # noqa: E402
from mqt.core.qdmi import ProgramFormat  # noqa: E402
from defw_exception import DEFwExecutionError  # noqa: E402

SERVICES = pathlib.Path(__file__).resolve().parents[2] / "services"


@pytest.fixture
def profile(monkeypatch):
	for name, path in (
			("svc_lib_qpm", SERVICES / "svc_lib_qpm"),
			("svc_lib_qpm.drivers", SERVICES / "svc_lib_qpm/drivers")):
		if name not in sys.modules:
			package = types.ModuleType(name)
			package.__path__ = [str(path)]
			monkeypatch.setitem(sys.modules, name, package)
	from svc_lib_qpm.drivers.qdmi_profiles import PasqalQdmiProfile
	return PasqalQdmiProfile({"provider": "pasqal", "provider_device_id": "EMU_FREE"})


def _sequence():
	sequence = pulser.Sequence(pulser.Register({"z": (-10, 0), "a": (10, 0)}), MockDevice)
	sequence.declare_channel("local", "rydberg_local", initial_target="z")
	sequence.add(pulser.Pulse.ConstantPulse(1000, math.pi, 0, 0), "local")
	return sequence


def test_native_pulser_program_preserves_register_order_and_format(profile):
	sequence = _sequence()
	sequence.measure("ground-rydberg")
	source = sequence.to_abstract_repr()
	program, fmt, measurement = profile.encode(None, source, {}, None)
	assert program == source
	assert list(pulser.Sequence.from_abstract_repr(program).register.qubit_ids) == ["z", "a"]
	assert getattr(ProgramFormat, fmt) == ProgramFormat.CUSTOM1
	assert measurement is None
	counts = {"01": 9, "10": 1}
	assert profile.result_counts(counts, measurement) == {"10": 9, "01": 1}


@pytest.mark.parametrize("source", ["not JSON", "{}", '[]'])
def test_invalid_programs_fail_before_submission(profile, source):
	with pytest.raises(DEFwExecutionError, match="invalid Pasqal Pulser JSON"):
		profile.encode(None, source, {}, None)


def test_unmeasured_sequence_is_rejected(profile):
	with pytest.raises(DEFwExecutionError, match="measurement"):
		profile.encode(None, _sequence().to_abstract_repr(), {}, None)


def test_unbound_variables_are_rejected(profile):
	sequence = _sequence()
	variable = sequence.declare_variable("duration", dtype=int)
	sequence.delay(variable, "local")
	sequence.measure("ground-rydberg")
	with pytest.raises(DEFwExecutionError, match="bound variables"):
		profile.encode(None, sequence.to_abstract_repr(), {}, None)


def test_mappable_registers_are_rejected(profile):
	from pulser.register import RegisterLayout
	register = RegisterLayout([(-30, 0), (-10, 0), (10, 0), (30, 0)]).make_mappable_register(2)
	sequence = pulser.Sequence(register, MockDevice)
	sequence.declare_channel("global", "rydberg_global")
	sequence.add(pulser.Pulse.ConstantPulse(1000, math.pi, 0, 0), "global")
	sequence.measure("ground-rydberg")
	with pytest.raises(DEFwExecutionError, match="mapped register"):
		profile.encode(None, sequence.to_abstract_repr(), {}, None)


@pytest.mark.parametrize("credential_mode", ["auth-file", "bound-token"])
def test_installed_device_opens_and_submits_against_local_cloud(profile, monkeypatch, tmp_path, credential_mode):
	# Only HTTP responses are synthetic; catalogue discovery, session opening,
	# native submission and result decoding run through the PyPI wheels.
	for name in ("PASQAL_EMAIL", "PASQAL_PASSWORD", "PASQAL_PROJECT_ID",
			"PASQAL_QDMI_BACKEND", "QFW_API_KEY", "QFW_QC_URL"):
		monkeypatch.delenv(name, raising=False)
	auth = tmp_path / "auth"
	auth.write_text("token=test-token\nproject_id=test-project\n")
	auth.chmod(0o600)
	monkeypatch.setenv("PASQAL_AUTHFILE", str(auth))
	sequence = _sequence()
	sequence.measure("ground-rydberg")
	program, fmt, _ = profile.encode(None, sequence.to_abstract_repr(), {}, None)
	submissions = []

	class Handler(BaseHTTPRequestHandler):
		def log_message(self, *args):
			pass

		def reply(self, value):
			data = json.dumps(value).encode()
			self.send_response(200)
			self.send_header("Content-Type", "application/json")
			self.send_header("Content-Length", str(len(data)))
			self.end_headers()
			self.wfile.write(data)

		def do_GET(self):
			if self.path.endswith("public-specs"):
				self.reply({"data": [{"device_type": "FRESNEL", "specs": MockDevice.to_abstract_repr()}]})
			elif "/devices" in self.path:
				self.reply({"data": [{"device_type": "EMU_FREE", "name": "EMU_FREE",
					"max_atom_num": 100, "is_emulator": True, "channels": [],
					"status": "UP", "active": True, "availability": "ACTIVE"}]})
			elif "/batches/" in self.path:
				self.reply({"data": {"id": "batch-1", "job_ids": ["job-1"], "queue_position": 2}})
			elif self.path.endswith("results_link"):
				self.reply({"data": {"results_link": base_url + "/results"}})
			elif self.path == "/results":
				self.reply({"counter": {"10": 9, "01": 1}})
			else:
				self.reply({"data": {"id": "job-1", "batch_id": "batch-1", "status": "DONE", "runs": 10,
					"sequence": program}})

		def do_POST(self):
			submissions.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
			self.reply({"data": {"id": "batch-1"}})

	with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
		base_url = f"http://127.0.0.1:{server.server_port}"
		thread = threading.Thread(target=server.serve_forever, daemon=True)
		thread.start()
		try:
			credential = None
			if credential_mode == "bound-token":
				credential = {"token": "test-token", "project_id": "test-project"}
			access = profile.access(credential)
			access["base_url"] = base_url
			device = profile.open_backend(access).device
			assert device.qubits_num() == 100
			assert device.regular_sites() == []
			assert profile.backend_info(device)["pulser_devices"] == {"FRESNEL": MockDevice.to_abstract_repr()}
			job = device.submit_job(program, getattr(ProgramFormat, fmt), 10)
			assert job.wait(timeout=5)
			assert job.get_counts() == {"01": 9, "10": 1}
			assert profile.result_counts(job.get_counts(), None) == {"10": 9, "01": 1}
			assert len(submissions) == 1
			assert submissions[0]["jobs"] == [{"sequence": program, "runs": 10}]
			assert submissions[0]["device_type"] == "EMU_FREE"
			assert submissions[0]["project_id"] == "test-project"
		finally:
			server.shutdown()
			thread.join(timeout=5)
