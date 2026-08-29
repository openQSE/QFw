import argparse
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))

from qfw_runtime import slurm_reservation


class QPM:
	def __init__(self, service_id, reservation_id=None, status="accepted"):
		self.service_id = service_id
		self.reservation_id = reservation_id
		self.status = status
		self.released = []

	def reserve(self, request):
		self.request = dict(request)
		return {
			"status": self.status,
			"reservation_id": self.reservation_id,
		}

	def release(self, reservation_id, reason):
		self.released.append((reservation_id, reason))
		return {"status": "accepted"}


class ReservedBinding:
	def __init__(self, client):
		self.client = client


class Resolver:
	def __init__(self, qpms):
		self.qpms = dict(qpms)
		self.lookups = []

	def connect_reserved(self, service_id, reservation_id, **kwargs):
		self.lookups.append((service_id, reservation_id, dict(kwargs)))
		return ReservedBinding(self.qpms[service_id])


def reserve_args(service_ids):
	return argparse.Namespace(
		service_id=list(service_ids),
		owner="user-a",
		job_id="41",
		allocation_id="41",
		walltime_seconds=300,
		ttl_seconds=600,
		timeout=10.0,
	)


def test_reserve_exports_complete_tuple_set(capsys):
	first = QPM("iqm-site", 41)
	second = QPM("nwqsim-site", 17)
	resolver = Resolver({
		"iqm-site": first,
		"nwqsim-site": second,
	})

	encoded = slurm_reservation.reserve(
		reserve_args(["iqm-site", "nwqsim-site"]), resolver=resolver)

	assert encoded == '[["iqm-site","41"],["nwqsim-site","17"]]'
	assert capsys.readouterr().out.strip() == (
		"QFW_RESERVATIONS=" + encoded)
	assert first.request["owner"] == {"user": "user-a"}
	assert first.request["job_id"] == "41"


def test_reserve_rolls_back_prior_qpms_on_partial_failure():
	first = QPM("iqm-site", 41)
	second = QPM("nwqsim-site", None, status="rejected")
	resolver = Resolver({
		"iqm-site": first,
		"nwqsim-site": second,
	})

	with pytest.raises(Exception, match="rejected reservation"):
		slurm_reservation.reserve(
			reserve_args(["iqm-site", "nwqsim-site"]), resolver=resolver)

	assert first.released == [("41", 0)]


def test_release_attempts_every_qpm():
	first = QPM("iqm-site")
	second = QPM("nwqsim-site")
	resolver = Resolver({
		"iqm-site": first,
		"nwqsim-site": second,
	})
	args = argparse.Namespace(
		reservations='[["iqm-site","41"],["nwqsim-site","17"]]',
		timeout=10.0,
	)

	assert slurm_reservation.release(args, resolver=resolver) == 0
	assert first.released == [("41", 0)]
	assert second.released == [("17", 0)]


def test_main_treats_reservation_output_as_success(monkeypatch):
	class Parser:
		@staticmethod
		def parse_args(argv):
			return argparse.Namespace(func=lambda args: '[["nwqsim","1"]]')

	monkeypatch.setattr(slurm_reservation, "build_parser", Parser)

	assert slurm_reservation.main([]) == 0
