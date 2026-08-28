import os

from qfw_qiskit.reservation_set import (
	parse_qfw_reservations,
	select_qpm_reservation,
)

def qfw_reservation_options(required=True, service_id=None):
	reservations = parse_qfw_reservations(required=required)
	if not reservations:
		return {}
	selected = select_qpm_reservation(reservations, service_id=service_id)
	options = {"reservation_id": selected.reservation_id}
	token = os.environ.get("QFW_RESERVATION_TOKEN")
	if token:
		options["token"] = token
	return options


def apply_qfw_reservation_to_backend(backend, required=True):
	for key, value in qfw_reservation_options(required=required).items():
		setattr(backend.options, key, value)
	return backend
