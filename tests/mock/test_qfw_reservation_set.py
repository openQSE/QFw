import pytest

from defw_exception import DEFwError
from qfw_qiskit.reservation_set import (
	QPMReservation,
	UINT64_MAX,
	encode_qfw_reservations,
	parse_qfw_reservations,
	select_qpm_reservation,
)


def test_reservation_set_round_trip_preserves_uint64():
	encoded = encode_qfw_reservations([
		QPMReservation("iqm-ornl-20q", UINT64_MAX),
	])

	assert encoded == '[["iqm-ornl-20q","18446744073709551615"]]'
	assert parse_qfw_reservations(encoded) == [
		QPMReservation("iqm-ornl-20q", UINT64_MAX),
	]


@pytest.mark.parametrize("value", [
	"not-json",
	"[]",
	'[["qpm-a",41]]',
	'[["qpm-a","0"]]',
	'[["qpm-a","18446744073709551616"]]',
])
def test_reservation_set_rejects_invalid_values(value):
	with pytest.raises(DEFwError):
		parse_qfw_reservations(value)


def test_multiple_reservations_require_service_selection():
	reservations = parse_qfw_reservations(
		'[["qpm-a","41"],["qpm-b","17"]]')

	with pytest.raises(DEFwError, match="service_id is required"):
		select_qpm_reservation(reservations)
	assert select_qpm_reservation(
		reservations, service_id="qpm-b").reservation_id == 17


def test_duplicate_service_reservations_are_rejected():
	with pytest.raises(DEFwError, match="duplicate service_id"):
		parse_qfw_reservations(
			'[["qpm-a","41"],["qpm-a","42"]]')
