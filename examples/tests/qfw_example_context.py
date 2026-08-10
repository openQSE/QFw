import os


def normalize_qfw_reservation_id(value):
	if isinstance(value, str):
		value = value.strip()
		if value.isdecimal():
			return int(value)
	return value


def qfw_reservation_options(required=True):
	reservation_id = os.environ.get("QFW_RESERVATION_ID")
	if not reservation_id:
		if required:
			raise RuntimeError(
				"QFW_RESERVATION_ID is required; launch through the QFw "
				"example reservation driver")
		return {}

	options = {"reservation_id": normalize_qfw_reservation_id(reservation_id)}
	token = os.environ.get("QFW_RESERVATION_TOKEN")
	if token:
		options["token"] = token
	return options


def apply_qfw_reservation_to_backend(backend, required=True):
	for key, value in qfw_reservation_options(required=required).items():
		setattr(backend.options, key, value)
	return backend
