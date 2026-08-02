import inspect


def test_qpm_telemetry_api_does_not_accept_token_placeholders():
	from api_qpm import QPMTelemetry

	for method_name in (
		"get_backend_info",
		"get_device_info",
		"get_dynamic_backend_info",
		"get_calibration_snapshot",
		"get_coupling_graph",
		"get_last_job_timing",
		"get_last_job_metadata",
	):
		signature = inspect.signature(getattr(QPMTelemetry, method_name))
		assert "token" not in signature.parameters


def test_simulator_metadata_methods_work_without_token_placeholder(monkeypatch):
	from svc_nwqsim_qpm.svc_qpm import QPM as NWQSimQPM
	from svc_tnqvm_qpm.svc_qpm import QPM as TNQVMQPM

	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")

	for qpm_class, backend in (
		(NWQSimQPM, "nwqsim"),
		(TNQVMQPM, "tnqvm"),
	):
		qpm = qpm_class(start=False)
		assert qpm.get_backend_info() == {
			"backend": backend,
			"metadata_supported": False,
		}
