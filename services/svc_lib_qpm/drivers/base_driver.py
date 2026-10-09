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
