# https://docs.quantum.ibm.com/api/qiskit/qiskit.providers.JobV1

from qiskit.providers import JobV1 as Job
from qiskit.providers.jobstatus import JobStatus
from qiskit.result import Result
import time

class QFWJob(Job):
	_async = True
	def __init__(self, backend, job_id, fn, qobj, options):
		super().__init__(backend, job_id)

		# do this on .result()
		self._result = fn(job_id, qobj, options)

		self._backend = backend
		self._qobj = qobj
		self._options = options

	def submit(self):
		# TODO: divide _run_async_job() and just have qfw.run_async() and returns a list of cids.
		# NOTE: change QPM api to accept batch of circuits in one RPC call.
		print("QFWJOB Submit called = ", self.job_id)
		return

	# TODO: this is blocking! needs to call .status() and return _result once all circuits are completed.
	def result(self):
		return self._result

	def status(self):
		return NotImplementedError
		print("QFWJOB status called = ", self.job_id)
		# query job_id and return one of -
			# JobStatus.INITIALIZING
			# JobStatus.QUEUED
			# JobStatus.VALIDATING
			# JobStatus.RUNNING
			# JobStatus.CANCELLED
			# JobStatus.DONE
			# JobStatus.ERROR
		# checking self._qfw_job_id
		try:
			res = qpm_api.read_cq(self._qfw_job_id)
		except Exception as e:
			if type(e) == DEFwInProgress:
				return JobStatus.RUNNING
			elif type(e) == DEFwError:
				return JobStatus.ERROR
			elif type(e) == DEFwNotFound:
				return JobStatus.ERROR

	def backend(self):
		return self._backend

	def qobj(self):
		return self._qobj

	def options(self):
		return self._options
