def test_parse_execution_request_preserves_opaque_token(monkeypatch):
	import util.qpm.request as qpm_request

	monkeypatch.setenv("QFW_QPM_AUTH_DISABLED", "yes")
	token = {"opaque": "token"}
	request = qpm_request.parse_execution_request(
		{"qasm": "OPENQASM 2.0;", "num_shots": 8,
		 "token": {"payload": "ignored"}},
		reservation_id="reservation-1",
		token=token,
	)

	assert request.context.reservation_id == "reservation-1"
	assert request.context.token is token
	assert request.context.auth_disabled is True
	assert request.payload["reservation_id"] == "reservation-1"
	assert "token" not in request.payload


def test_parse_execution_request_preserves_scoped_metadata(monkeypatch):
	import util.qpm.request as qpm_request

	monkeypatch.setenv("QFW_QPM_AUTH_DISABLED", "yes")
	owner = {"user": "alice"}
	run_context = {"priority": "normal"}
	request = qpm_request.parse_execution_request(
		{"qasm": "OPENQASM 2.0;"},
		reservation_id="reservation-1",
		owner=owner,
		job_id="job-7",
		run_context=run_context,
	)

	assert request.context.reservation_id == "reservation-1"
	assert request.context.metadata == {
		"owner": owner,
		"job_id": "job-7",
		"run_context": run_context,
	}
	assert "owner" not in request.payload
	assert "job_id" not in request.payload
	assert "run_context" not in request.payload


def test_parse_execution_request_moves_scoped_payload_metadata(monkeypatch):
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
	assert request.context.metadata == {
		"owner": {"user": "alice"},
		"job_id": "job-7",
		"run_context": {"priority": "normal"},
	}
	assert request.context.auth_disabled is True


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
