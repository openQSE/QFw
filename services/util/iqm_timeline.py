"""
IQM's job timeline, as QRMI's task_logs prints it, into phase durations.

QRMI's IQM resource returns a job's measurements only. What the server
recorded about the job, one timestamped event per phase (created,
received, validation, compilation, execution, post-processing, ready,
completed), reaches us as text from task_logs, one event per line:

    Timeline   :
      2026-10-12 18:39:22.123 [station                 ] execution_started
      2026-10-12 18:39:24.971 [station                 ] execution_ended
    Messages   : (none)

The native IQM QPM gets the same events structured and summarizes them in
util_iqm.build_timing_summary; the durations here are the same ones, by the
same names, so the vendor histogram reads alike on both paths. Timestamps
are the server's own and are compared only with each other. This lives in
util, beside the instrumentation, so a test can import it without booting
a service package.
"""
import re
from datetime import datetime

_EVENT = re.compile(
	r"^\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}) \[(.*?)\s*\] (\S+)\s*$")

# (phase, from this status, to that status); each status is its first
# occurrence in the timeline.
PHASES = (
	("queue", "received", "validation_started"),
	("validation", "validation_started", "validation_ended"),
	("compilation", "compilation_started", "compilation_ended"),
	("execution", "execution_started", "execution_ended"),
	("post_processing", "post_processing_started", "post_processing_ended"),
	("total", "created", "completed"),
)


def parse_task_logs(text):
	"""The timeline's (time, source, status) events, in order, from the text."""
	events = []
	for line in (text or "").splitlines():
		match = _EVENT.match(line)
		if not match:
			continue
		try:
			when = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S.%f")
		except ValueError:
			continue
		events.append((when, match.group(2).strip(), match.group(3)))
	return events


def phase_durations(events):
	"""Seconds per phase from the events; a phase whose bounds are missing is left out."""
	first = {}
	for when, _source, status in events:
		first.setdefault(status, when)
	durations = {}
	for phase, start, end in PHASES:
		if start in first and end in first:
			seconds = (first[end] - first[start]).total_seconds()
			if seconds >= 0:
				durations[phase] = seconds
	return durations


def durations_from_task_logs(text):
	"""Phase durations straight from task_logs' text; {} when it holds no timeline."""
	return phase_durations(parse_task_logs(text))
