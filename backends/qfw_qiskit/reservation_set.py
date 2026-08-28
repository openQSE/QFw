import json
import os
from dataclasses import dataclass

from defw_exception import DEFwError


QFW_RESERVATIONS_ENV = "QFW_RESERVATIONS"
UINT64_MAX = (1 << 64) - 1


@dataclass(frozen=True)
class QPMReservation:
	service_id: str
	reservation_id: str


def parse_qfw_reservations(value=None, required=True):
	if value is None:
		value = os.environ.get(QFW_RESERVATIONS_ENV)
	if not value:
		if required:
			raise DEFwError(
				f"{QFW_RESERVATIONS_ENV} is required for managed QPM access")
		return []
	try:
		parsed = json.loads(value)
	except (TypeError, json.JSONDecodeError) as exc:
		raise DEFwError(
			f"{QFW_RESERVATIONS_ENV} must be valid JSON: {exc}") from exc
	if not isinstance(parsed, list) or not parsed:
		raise DEFwError(
			f"{QFW_RESERVATIONS_ENV} must be a non-empty JSON list")
	reservations = [
		_parse_tuple(item, index) for index, item in enumerate(parsed)
	]
	seen = set()
	for reservation in reservations:
		if reservation.service_id in seen:
			raise DEFwError(
				f"{QFW_RESERVATIONS_ENV} contains duplicate service_id "
				f"{reservation.service_id!r}")
		seen.add(reservation.service_id)
	return reservations


def select_qpm_reservation(reservations, service_id=None):
	reservations = list(reservations)
	if service_id is not None:
		matches = [
			item for item in reservations if item.service_id == service_id
		]
		if len(matches) != 1:
			raise DEFwError(
				f"reservation set does not contain exactly one service "
				f"{service_id!r}")
		return matches[0]
	if len(reservations) != 1:
		raise DEFwError(
			"service_id is required when multiple QPMs are reserved")
	return reservations[0]


def encode_qfw_reservations(reservations):
	return json.dumps([
		[item.service_id, item.reservation_id]
		for item in reservations
	], separators=(",", ":"))


def _parse_tuple(value, index):
	if not isinstance(value, list) or len(value) != 2:
		raise DEFwError(
			f"{QFW_RESERVATIONS_ENV} entry {index} must be a two-item list")
	service_id, reservation_id = value
	if not isinstance(service_id, str) or not service_id.strip():
		raise DEFwError(
			f"{QFW_RESERVATIONS_ENV} entry {index} has invalid service_id")
	if not isinstance(reservation_id, str) or not reservation_id.isdecimal():
		raise DEFwError(
			f"{QFW_RESERVATIONS_ENV} entry {index} reservation_id must be "
			"a decimal string")
	number = int(reservation_id, 10)
	if number == 0 or number > UINT64_MAX:
		raise DEFwError(
			f"{QFW_RESERVATIONS_ENV} entry {index} reservation_id must be "
			"between 1 and UINT64_MAX")
	return QPMReservation(service_id.strip(), reservation_id)
