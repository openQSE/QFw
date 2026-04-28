import json
import logging
import os
import shlex
import shutil
import socket
import subprocess
import sys

import defw_common_def as common
from defw_agent_info import Capability, DEFwServiceInfo
from defw_exception import DEFwError


class MPISmoke:
	def __init__(self, start=True):
		self._ref_count = 0

	def query(self):
		from . import SERVICE_DESC, SERVICE_NAME

		logging.debug("Querying MPI smoke service metadata")

		cap = Capability(1, 1, "launches a trivial mpirun smoke workload")
		return DEFwServiceInfo(
			SERVICE_NAME,
			SERVICE_DESC,
			self.__class__.__name__,
			self.__class__.__module__,
			cap,
			-1,
		)

	def reserve(self, svc, client_ep, *args, **kwargs):
		logging.debug("Reserving MPI smoke service for client %s", client_ep)
		self._ref_count += 1
		return None

	def release(self, services=None):
		logging.debug("Releasing MPI smoke service")
		if self._ref_count > 0:
			self._ref_count -= 1
		return None

	def run_pid_hello(self, np=2):
		logging.info("Running MPI smoke hello workload with %d ranks", np)
		if np < 1:
			raise DEFwError("np must be >= 1")

		payload = (
			"import json, os, socket;"
			"print(json.dumps({"
			"'host': socket.gethostname(),"
			"'pid': os.getpid(),"
			"'rank': int(os.environ.get(\"OMPI_COMM_WORLD_RANK\", \"0\"))"
			"}))"
		)
		python = shutil.which("python3_defw_orig") or sys.executable

		cmd = ["mpirun"]
		if os.geteuid() == 0:
			cmd.append("--allow-run-as-root")
		dvm_uri = os.environ.get("QFW_DVM_URI_PATH", "").strip()
		if dvm_uri:
			cmd.extend(["--dvm", f"file:{dvm_uri}"])
		cmd.extend(["-np", str(np), python, "-c", payload])

		logging.debug("MPI smoke command: %s", shlex.join(cmd))

		result = subprocess.run(
			cmd,
			capture_output=True,
			text=True,
			check=False,
		)

		logging.debug(
			"MPI smoke command completed: rc=%s stdout=%r stderr=%r",
			result.returncode,
			result.stdout,
			result.stderr,
		)

		records = []
		for line in result.stdout.splitlines():
			line = line.strip()
			if not line:
				continue
			try:
				records.append(json.loads(line))
			except json.JSONDecodeError:
				records.append({"raw": line})

		return {
			'service_host': socket.gethostname(),
			'service_pid': os.getpid(),
			'rc': result.returncode,
			'stdout': result.stdout,
			'stderr': result.stderr,
			'records': records,
		}

	def shutdown(self):
		logging.debug(
			"Shutting down MPI smoke service; ref_count=%d",
			self._ref_count,
		)
		if self._ref_count > 0:
			self._ref_count -= 1
		if self._ref_count == 0:
			common.shutdown_service_instance(self)
			return True
		return False
