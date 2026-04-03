from tests.mock.fakes import FakeQPM


def test_get_qpm_returns_reserved_service(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM()

	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: "rmgr")
	monkeypatch.setattr(
		lookup_service,
		"defw_reserve_service_by_name",
		lambda rmgr, name, qpm_type, qpm_cap: [fake_qpm],
	)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is fake_qpm
	assert fake_qpm.shutdown_called is False


def test_get_qpm_shuts_down_failed_service_probe(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	fake_qpm = FakeQPM(test_error=RuntimeError("probe failed"))

	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: "rmgr")
	monkeypatch.setattr(
		lookup_service,
		"defw_reserve_service_by_name",
		lambda rmgr, name, qpm_type, qpm_cap: [fake_qpm],
	)

	result = lookup_service.get_qpm(qpm_type=4, qpm_cap=2)

	assert result is fake_qpm
	assert fake_qpm.shutdown_called is True


def test_get_qpm_propagates_reservation_failures(monkeypatch):
	import qfw_qiskit.qfw_lookup_service as lookup_service

	monkeypatch.setattr(lookup_service, "defw_get_resource_mgr", lambda: "rmgr")

	def raise_reservation_error(*args, **kwargs):
		raise RuntimeError("reserve failed")

	monkeypatch.setattr(
		lookup_service,
		"defw_reserve_service_by_name",
		raise_reservation_error,
	)

	try:
		lookup_service.get_qpm(qpm_type=4, qpm_cap=2)
	except RuntimeError as exc:
		assert str(exc) == "reserve failed"
	else:
		raise AssertionError("expected reservation failure to propagate")
