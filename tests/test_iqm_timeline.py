import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

from util.iqm_timeline import (  # noqa: E402
	durations_from_task_logs, parse_task_logs, phase_durations)

# What QRMI's IQM resource prints from task_logs: the job's timeline, one
# server-stamped event per line, then the messages.
TASK_LOGS = """Timeline   :
  2026-10-12 18:39:20.000 [server                  ] created
  2026-10-12 18:39:20.250 [station                 ] received
  2026-10-12 18:39:20.300 [station                 ] validation_started
  2026-10-12 18:39:20.400 [station                 ] validation_ended
  2026-10-12 18:39:20.500 [station                 ] compilation_started
  2026-10-12 18:39:21.000 [station                 ] compilation_ended
  2026-10-12 18:39:22.000 [station                 ] execution_started
  2026-10-12 18:39:24.500 [station                 ] execution_ended
  2026-10-12 18:39:24.600 [station                 ] post_processing_started
  2026-10-12 18:39:24.700 [station                 ] post_processing_ended
  2026-10-12 18:39:24.800 [server                  ] ready
  2026-10-12 18:39:25.000 [server                  ] completed
Messages   : (none)
"""


def test_the_timeline_is_read_from_the_text():
	events = parse_task_logs(TASK_LOGS)
	assert len(events) == 12
	assert events[0][1:] == ("server", "created")
	assert events[6][1:] == ("station", "execution_started")
	assert (events[7][0] - events[6][0]).total_seconds() == 2.5


def test_phase_durations_follow_the_native_summary():
	assert durations_from_task_logs(TASK_LOGS) == {
		"queue": 0.05,
		"validation": 0.1,
		"compilation": 0.5,
		"execution": 2.5,
		"post_processing": 0.1,
		"total": 5.0,
	}


def test_a_phase_without_both_bounds_is_left_out():
	partial = "\n".join(
		line for line in TASK_LOGS.splitlines() if "execution_ended" not in line)
	durations = durations_from_task_logs(partial)
	assert "execution" not in durations
	assert durations["total"] == 5.0


def test_repeated_statuses_count_from_their_first_event():
	events = parse_task_logs(TASK_LOGS) + parse_task_logs(TASK_LOGS)
	assert phase_durations(events)["execution"] == 2.5


def test_text_without_a_timeline_gives_nothing():
	assert durations_from_task_logs("") == {}
	assert durations_from_task_logs("Messages   : (none)\n") == {}
	assert durations_from_task_logs(None) == {}
