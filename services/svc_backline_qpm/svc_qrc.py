# The Backline QPM serves placements, not executions: the client compiles its
# program with Catalyst and runs it on the reserved controller and
# coprocessors itself. Execution calls through QRI are refused.

from defw_exception import DEFwExecutionError

_REFUSAL = (
	"backline-local is a client-executed resource: reserve it, read the "
	"placement from the reservation, and run the program on that placement")


class QRC:
	def __init__(self, start=True):
		self.push_info = {}

	def sync_run(self, circuit):
		raise DEFwExecutionError(_REFUSAL)

	def async_run(self, circuit):
		raise DEFwExecutionError(_REFUSAL)

	def cancel(self, provider_handle):
		return "not-found"

	def register_event_notification(self, info):
		self.push_info = dict(info or {})

	def read_cq(self, cid=None):
		return None

	def peak_cq(self, cid=None):
		return None

	def get_task_timing(self, cid=None):
		return {}

	def get_task_metadata(self, cid=None):
		return {}

	def shutdown(self):
		pass
