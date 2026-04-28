from defw_remote import BaseRemote


class MPISmoke(BaseRemote):
	def __init__(self, si):
		super().__init__(service_info=si)

	def run_pid_hello(self, np=2):
		pass

	def shutdown(self):
		pass
