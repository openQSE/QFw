def test_parse_execution_request_preserves_opaque_token(monkeypatch):
	import util.qpm.request as qpm_request

	monkeypatch.setenv("QFW_QPM_AUTH_DISABLED", "yes")
	request = qpm_request.parse_execution_request(
		{"qasm": "OPENQASM 2.0;", "num_shots": 8},
		reservation_id="reservation-1",
		token={"opaque": "token"},
	)

	assert request.context.reservation_id == "reservation-1"
	assert request.context.token == {"opaque": "token"}
	assert request.context.auth_disabled is True
	assert request.payload["reservation_id"] == "reservation-1"
	assert request.payload["token"] == {"opaque": "token"}


def test_parse_execution_request_ignores_scoped_metadata_overrides(monkeypatch):
	import util.qpm.request as qpm_request

	monkeypatch.setenv("QFW_QPM_AUTH_DISABLED", "yes")
	request = qpm_request.parse_execution_request(
		{"qasm": "OPENQASM 2.0;"},
		reservation_id="reservation-1",
		owner={"user": "alice"},
		job_id="job-7",
		run_context={"priority": "normal"},
	)

	assert request.context.reservation_id == "reservation-1"
	assert not hasattr(request.context, "owner")
	assert "owner" not in request.payload
	assert "job_id" not in request.payload
	assert "run_context" not in request.payload


def test_parse_execution_request_strips_scoped_payload_metadata(monkeypatch):
	import util.qpm.request as qpm_request

	monkeypatch.delenv("QFW_QPM_AUTH_DISABLED", raising=False)
	payload = {
		"qasm": "OPENQASM 2.0;",
		"num_shots": 8,
		"owner": {"user": "alice"},
		"job_id": "job-7",
		"run_context": {"priority": "normal"},
	}
	request = qpm_request.parse_execution_request(payload)

	assert request.payload == {"qasm": "OPENQASM 2.0;", "num_shots": 8}
	assert payload["owner"] == {"user": "alice"}
	assert request.context.reservation_id is None
	assert request.context.token is None
	assert request.context.auth_disabled is True


def test_parse_execution_request_preserves_false_token(monkeypatch):
	import util.qpm.request as qpm_request

	monkeypatch.setenv("QFW_QPM_AUTH_DISABLED", "yes")
	request = qpm_request.parse_execution_request(
		{"qasm": "OPENQASM 2.0;"},
		token=False,
	)

	assert request.context.token is False
	assert request.payload["token"] is False


def test_status_envelope_omits_empty_fields():
	import util.qpm.request as qpm_request

	envelope = qpm_request.status_envelope(
		"rejected",
		reason="invalid-reservation",
		reservation_id="reservation-1",
	)

	assert envelope == {
		"status": "rejected",
		"reason": "invalid-reservation",
		"reservation_id": "reservation-1",
	}
