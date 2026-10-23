# Base class for shim drivers. A driver adapts one lower-level library
# (QRMI, QDMI, …) to the QFw front-end contract and declares which contract
# calls it implements (its capability set). Unimplemented calls are NULL-ed
# out — the Frontend never routes a call to a driver that does not implement it.

from defw_exception import DEFwExecutionError
import collections
import threading


def reservation_cache_key(reservation_id):
	# The key a driver caches what it opens for a reservation under, the way
	# the native IQM QPM keys its clients (svc_iqm_qpm/util_iqm.py), so it can
	# be dropped when the reservation ends.
	return ("reservation", str(reservation_id))


# How long a driver waits between status polls of a provider job. A circuit
# that names a poll_interval is polled at that fixed interval, as before.
# Otherwise the first wait is the device's poll-interval-seconds, else
# DEFAULT_POLL_INTERVAL, and each poll that finds the job unfinished doubles
# it, up to MAX_POLL_INTERVAL, which is how IQM's own QDMI library waits for a
# job. A provider that limits its request rate counts every poll, and a job
# that queues for minutes would otherwise be polled every second throughout.
DEFAULT_POLL_INTERVAL = 1.0
MAX_POLL_INTERVAL = 60.0


def poll_schedule(info, descriptor):
	# The first wait between polls, and whether later waits double. Read
	# before anything is submitted, so a bad value fails with nothing at the
	# provider.
	value = info.get("poll_interval")
	backoff = value is None
	if backoff:
		value = descriptor.get("poll_interval_seconds")
	if value in (None, ""):
		value = DEFAULT_POLL_INTERVAL
	try:
		wait = float(value)
	except (TypeError, ValueError):
		raise DEFwExecutionError(
			f"the poll interval must be a number of seconds, not {value!r} "
			"(poll-interval-seconds or the client's poll_interval)") from None
	return max(wait, 0.0), backoff


def next_poll_wait(wait, backoff):
	# The wait after the next poll that finds the job unfinished. A first
	# wait above the cap stays where it is.
	if not backoff:
		return wait
	return max(wait, min(MAX_POLL_INTERVAL, wait * 2))


def pasqal_backend_info(device_type, entries):
	# get_backend_info for a Pasqal device: the Pulser devices it runs, by
	# name, in Pulser's abstract representation. entries are Pasqal Cloud's
	# [{"device_type", "specs"}], the one device for a QPU and the real
	# devices' public specs for an emulator, which runs a sequence built for
	# any of them.
	return {
		"backend": "pasqal",
		"metadata_supported": True,
		"device_type": device_type,
		"pulser_devices": {
			str(entry["device_type"]): entry["specs"] for entry in entries},
	}


class JobRecords:
	# The jobs a driver ran, by cid, for get_task_timing and
	# get_task_metadata. A lookup names its cid, so one user's call never
	# reads another user's job. Bounded, oldest out first, so a long-running
	# service does not keep every job it ran.
	LIMIT = 1024

	def __init__(self, library, limit=LIMIT):
		self._library = library
		self._limit = limit
		self._records = collections.OrderedDict()
		self._lock = threading.Lock()

	def put(self, job):
		cid = job.get("cid")
		if cid is None:
			return
		with self._lock:
			self._records[str(cid)] = job
			self._records.move_to_end(str(cid))
			while len(self._records) > self._limit:
				self._records.popitem(last=False)

	def get(self, cid):
		if cid is None:
			raise DEFwExecutionError(
				f"{self._library} task lookups need the task's cid")
		with self._lock:
			job = self._records.get(str(cid))
		if job is None:
			raise DEFwExecutionError(
				f"{self._library} has no job for cid {cid!r}")
		return job


class BaseDriver:
	# Subclasses set `name` (the library key used for routing/preference) and
	# `CAPABILITIES` (the subset of contract calls they cover).
	name = "base"
	CAPABILITIES = frozenset()

	def implements(self, call):
		return call in self.CAPABILITIES

	def evict_reservation(self, reservation_id):
		# Drop what this driver opened for a reservation that has ended. The
		# QPM controller calls it through the shim QRC when the reservation
		# is released, cancelled or expires. True if anything was dropped.
		return False
