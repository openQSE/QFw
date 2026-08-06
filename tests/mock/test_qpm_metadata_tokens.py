import inspect
import sys
import types


def test_qpm_telemetry_api_accepts_token_placeholders():
	from api_qpm import QPMTelemetry

	expected_parameters = {
		"get_backend_info": ["self", "lib", "token"],
		"get_device_info": ["self", "lib", "token"],
		"get_dynamic_backend_info": [
			"self", "calibration_set_id", "lib", "token"],
		"get_calibration_snapshot": [
			"self", "calibration_set_id", "lib", "token"],
		"get_coupling_graph": [
			"self", "calibration_set_id", "lib", "token"],
		"get_last_job_timing": [
			"self", "cid", "lib", "reservation_id", "token"],
		"get_last_job_metadata": [
			"self", "cid", "lib", "reservation_id", "token"],
	}

	for method_name, parameters in expected_parameters.items():
		signature = inspect.signature(getattr(QPMTelemetry, method_name))
		assert "token" in signature.parameters
		assert list(signature.parameters) == parameters


def test_simulator_metadata_methods_accept_token_placeholder(monkeypatch):
	from svc_nwqsim_qpm.svc_qpm import QPM as NWQSimQPM
	from svc_tnqvm_qpm.svc_qpm import QPM as TNQVMQPM

	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")

	for qpm_class, backend in (
		(NWQSimQPM, "nwqsim"),
		(TNQVMQPM, "tnqvm"),
	):
		qpm = qpm_class(start=False)
		assert qpm.get_backend_info(token={"opaque": "token"}) == {
			"backend": backend,
			"metadata_supported": False,
		}


def _assert_full_metadata_category_shape(qpm, backend):
	token = {"opaque": "token"}
	lib = "ignored-lib"
	calibration_set_id = "calibration-1"
	cid = "cid-1"
	reservation_id = "reservation-1"

	assert qpm.get_backend_info(lib, token)["backend"] == backend
	assert qpm.get_device_info(lib, token)["backend"] == backend

	dynamic_info = qpm.get_dynamic_backend_info(
		calibration_set_id, lib, token)
	assert dynamic_info["backend"] == backend
	assert dynamic_info["calibration_set_id"] == calibration_set_id

	calibration = qpm.get_calibration_snapshot(
		calibration_set_id, lib, token)
	assert calibration["backend"] == backend
	assert calibration["calibration_set_id"] == calibration_set_id

	coupling = qpm.get_coupling_graph(calibration_set_id, lib, token)
	assert coupling["backend"] == backend
	assert coupling["calibration_set_id"] == calibration_set_id

	timing = qpm.get_last_job_timing(cid, lib, reservation_id, token)
	assert timing["backend"] == backend
	assert timing["cid"] == cid

	metadata = qpm.get_last_job_metadata(cid, lib, reservation_id, token)
	assert metadata["backend"] == backend
	assert metadata["cid"] == cid


def test_non_shim_provider_metadata_methods_accept_full_category_shape(
		monkeypatch):
	from svc_iqm_qpm.svc_qpm import QPM as IQMQPM
	from svc_nwqsim_qpm.svc_qpm import QPM as NWQSimQPM
	_install_qb_import_stubs()
	from svc_qb_qpm.svc_qpm import QPM as QBQPM
	from svc_tnqvm_qpm.svc_qpm import QPM as TNQVMQPM

	class FakeIQMQRC:
		def __init__(self):
			self.calls = []

		def get_backend_info(self):
			self.calls.append(("get_backend_info",))
			return {
				"backend": "iqm",
				"metadata_supported": True,
			}

		def get_device_info(self):
			self.calls.append(("get_device_info",))
			return {
				"backend": "iqm",
				"metadata_supported": True,
			}

		def get_dynamic_backend_info(self, calibration_set_id=None):
			self.calls.append((
				"get_dynamic_backend_info", calibration_set_id))
			return {
				"backend": "iqm",
				"calibration_set_id": calibration_set_id,
			}

		def get_calibration_snapshot(self, calibration_set_id=None):
			self.calls.append((
				"get_calibration_snapshot", calibration_set_id))
			return {
				"backend": "iqm",
				"calibration_set_id": calibration_set_id,
			}

		def get_coupling_graph(self, calibration_set_id=None):
			self.calls.append(("get_coupling_graph", calibration_set_id))
			return {
				"backend": "iqm",
				"calibration_set_id": calibration_set_id,
			}

		def get_last_job_timing(self, cid=None):
			self.calls.append(("get_last_job_timing", cid))
			return {
				"backend": "iqm",
				"cid": cid,
			}

		def get_last_job_metadata(self, cid=None):
			self.calls.append(("get_last_job_metadata", cid))
			return {
				"backend": "iqm",
				"cid": cid,
			}

	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")

	for qpm_class, backend in (
		(NWQSimQPM, "nwqsim"),
		(QBQPM, "qb"),
		(TNQVMQPM, "tnqvm"),
	):
		_assert_full_metadata_category_shape(
			qpm_class(start=False), backend)

	qpm = IQMQPM.__new__(IQMQPM)
	qpm.qrc = FakeIQMQRC()
	_assert_full_metadata_category_shape(qpm, "iqm")
	assert qpm.qrc.calls == [
		("get_backend_info",),
		("get_device_info",),
		("get_dynamic_backend_info", "calibration-1"),
		("get_calibration_snapshot", "calibration-1"),
		("get_coupling_graph", "calibration-1"),
		("get_last_job_timing", "cid-1"),
		("get_last_job_metadata", "cid-1"),
	]


def _install_qb_import_stubs():
	if "defw_cmd" not in sys.modules:
		defw_cmd = types.ModuleType("defw_cmd")
		defw_cmd.defw_exec_remote_cmd = lambda *args, **kwargs: None
		sys.modules["defw_cmd"] = defw_cmd
	if "requests" not in sys.modules:
		try:
			__import__("requests")
		except ImportError:
			requests = types.ModuleType("requests")
			requests.get = lambda url: None
			sys.modules["requests"] = requests
