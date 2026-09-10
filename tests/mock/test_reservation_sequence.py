import pytest

from util.qpm.admission import QPMAdmissionValidationError
from util.qpm.reservation_sequence import (
	PersistentReservationSequence,
	UINT64_MAX,
)


def test_sequence_persists_across_allocator_instances(tmp_path):
	first = PersistentReservationSequence(tmp_path)

	assert first.allocate() == 1
	assert PersistentReservationSequence(tmp_path).allocate() == 2
	assert (tmp_path / "state" / "reservation-sequence").read_text(
		encoding="utf-8") == "2\n"


def test_sequence_wraps_and_skips_active_reservations(tmp_path):
	sequence_path = tmp_path / "state" / "reservation-sequence"
	sequence_path.parent.mkdir()
	sequence_path.write_text(f"{UINT64_MAX}\n", encoding="utf-8")
	sequence = PersistentReservationSequence(tmp_path)

	assert sequence.allocate(active_ids={1, 2}) == 3


def test_corrupt_sequence_prevents_startup(tmp_path):
	sequence_path = tmp_path / "state" / "reservation-sequence"
	sequence_path.parent.mkdir()
	sequence_path.write_text("not-a-number\n", encoding="utf-8")

	with pytest.raises(QPMAdmissionValidationError, match="invalid"):
		PersistentReservationSequence(tmp_path)
