"""Host slots that a host:slots entry gives.

QFW_QPM_ASSIGNED_HOSTS can give a host its own slot count, as host:slots.
The shim builds its QPM with one slot a host (max_ppn=1), so
`assigned-hosts: <node>:8` gives it eight. Giving a slot back used to check
the host against max_ppn instead, so it raised "Returning more resources
than originally had" and kept the slot. When a provider start failed, that
error also hid the start's own.
"""

import pytest

import util.qpm.util_qpm as util_qpm
from tests.mock.fakes import FakeSchedulerContext
from tests.mock.test_qpm_dispatch_races import _finish, _outcome, _run
from tests.mock.test_qpm_scheduler import FakeAdmissionContext, FakeQRC
from util.qpm.controller import _clear_target_controllers_for_tests
from util.qpm.util_qpm import UTIL_QPM


class OneRunningTaskSchedulerContext(FakeSchedulerContext):
	"""Refuses a second running task, as the native qhw_scheduler does
	while one task holds its QPU's single execution slot (rc=-7)."""

	def task_started(self, task_id):
		if "running" in self.states.values():
			raise RuntimeError("failed to mark task started: rc=-7")
		super().task_started(task_id)


class OneSlotPerHostQPM(UTIL_QPM):
	# max_ppn=1, as the shim, IQM and fake IQM QPMs build theirs.
	def __init__(self, scheduler_context_factory=FakeSchedulerContext):
		self.fake_qrc = FakeQRC()
		super().__init__(
			self.fake_qrc,
			max_ppn=1,
			target_id="host-slots",
			admission_context_factory=FakeAdmissionContext,
			scheduler_context_factory=scheduler_context_factory,
		)


@pytest.fixture
def three_slots(monkeypatch):
	_clear_target_controllers_for_tests()
	FakeAdmissionContext.usage_status = "accepted"
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:3")
	monkeypatch.delenv(
		"QFW_QPM_COMPLETION_TERMINAL_RESERVATION_RETENTION_SECONDS",
		raising=False)
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	yield
	_clear_target_controllers_for_tests()


def test_a_host_slots_entry_sets_the_hosts_slot_count(three_slots):
	qpm = OneSlotPerHostQPM()

	assert qpm.free_hosts == {"localhost": 3}
	assert qpm.host_slots == {"localhost": 3}


def test_every_slot_comes_back_to_a_host_with_more_than_max_ppn(
		three_slots):
	qpm = OneSlotPerHostQPM()
	qpm.configure_dispatch_limits(limits={"max_inflight": 3})
	jobs = [_run(qpm) for _ in range(3)]
	assert qpm.fake_qrc.async_cids == [job["cid"] for job in jobs]
	assert qpm.free_hosts == {"localhost": 0}

	for job in jobs:
		_finish(qpm, job["cid"])

	assert qpm.free_hosts == {"localhost": 3}
	assert [_outcome(qpm, job["cid"]) for job in jobs] == ["COMPLETED"] * 3


def test_a_refused_start_fails_its_task_and_gives_its_slot_back(three_slots):
	# openQSE/QFw#101 as a Pasqal run met it: three slots, two allowed at
	# the provider, and a scheduler that runs one task at a time.
	qpm = OneSlotPerHostQPM(
		scheduler_context_factory=OneRunningTaskSchedulerContext)
	qpm.configure_dispatch_limits(limits={"max_inflight": 2})
	first = _run(qpm)

	second = _run(qpm)

	# The second task fails with the scheduler's refusal, not with a
	# slot accounting error, and its slot comes back.
	assert qpm.fake_qrc.async_cids == [first["cid"]]
	assert second["outcome"] == "FAILED"
	assert second["reason"] == "provider-submission-failed"
	assert qpm.free_hosts == {"localhost": 2}
	_finish(qpm, first["cid"])
	assert qpm.free_hosts == {"localhost": 3}
	assert _outcome(qpm, first["cid"]) == "COMPLETED"
