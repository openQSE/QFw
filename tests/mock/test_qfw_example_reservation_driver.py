import argparse

from qfw_qiskit.reservation_set import QPMReservation

import qfw_example_reservation_driver as driver


def _args():
	return argparse.Namespace(backend="nwqsim", timeout=1)


def test_reserve_uses_scheduler_owned_reservation(monkeypatch):
	resolved = type("Resolved", (), {"service_id": "nwqsim"})()
	qpm = type("QPM", (), {
		"reserve": lambda self, request: (_ for _ in ()).throw(
			AssertionError("reserve must not be called"))
	})()
	emitted = []
	monkeypatch.setattr(driver, "resolve_qpm", lambda *_: (resolved, qpm))
	monkeypatch.setattr(
		driver,
		"parse_qfw_reservations",
		lambda required: [QPMReservation("nwqsim", "41")],
	)
	monkeypatch.setattr(
		driver, "emit", lambda kind, **payload: emitted.append((kind, payload))
	)

	assert driver.reserve(_args()) == 0
	assert emitted == [(
		"reserve",
		{
			"backend": "nwqsim",
			"service_id": "nwqsim",
			"ownership": "scheduler",
			"decision": {"status": "accepted", "reservation_id": "41"},
		},
	)]
