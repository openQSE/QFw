from defw_agent_info import *  # noqa: F401,F403
import logging
import sys
import os
import json
from defw_exception import DEFwError, DEFwExecutionError
from util.mpi import backend_wrapper, build_mpi_command_string
from util.qpm.util_qrc import UTIL_QRC

sys.path.append(os.path.split(os.path.abspath(__file__))[0])


class QRC(UTIL_QRC):
	def __init__(self, start=True):
		super().__init__(start=start)

	def parse_result(self, out):
		try:
			out_str = out.decode("utf-8")
			if out_str == "":
				raise DEFwError({"Error": "Empty output!"})
				return {"Error": "Empty output!"}

			json_start = out_str.find('{')
			json_end = out_str.rfind('}')
			if json_start == -1 or json_end == -1 or json_end < json_start:
				raise DEFwError({"Error": "Could not parse result!"})
				return {"Error": "Could not parse result!"}

			payload = json.loads(out_str[json_start:json_end + 1])
			counts = payload.get('AcceleratorBuffer', {}).get('Measurements', {})
			if not counts:
				raise DEFwError({"Error": "Could not parse result!"})
				return {"Error": "Could not parse result!"}

			return counts
		except Exception as e:
			raise DEFwError({"Error": str(e)})
			return {"Error": str(e)}

	def form_cmd(self, circ, qasm_file):
		import shutil

		info = circ.info

		logging.debug(f"Circuit Info = {info}")

		if 'compiler' not in info:
			compiler = 'staq'
		else:
			compiler = info['compiler']

		circuit_runner = shutil.which(info['qfw_backend'])

		if not circuit_runner:
			logging.debug(f"{os.environ['PATH']}")
			logging.debug(f"{os.environ['LD_LIBRARY_PATH']}")
			raise DEFwExecutionError("Couldn't find circuit_runner. Check paths")

		dvm = os.environ.get("QFW_DVM_URI_PATH", "").strip()
		if dvm and not os.path.exists(dvm):
			raise DEFwExecutionError(f"dvm-uri {dvm} doesn't exist")

		executable = circuit_runner
		executable_args = []
		wrapper = backend_wrapper('tnqvm')
		if wrapper:
			executable = shutil.which(wrapper)
			if not executable:
				raise DEFwExecutionError(f"Couldn't find {wrapper}. Check paths")
			executable_args.extend(['-v', circuit_runner])

		executable_args.extend([
			'-q', qasm_file,
			'-b', info["num_qubits"],
			'-s', info["num_shots"],
			'-c', compiler
		])

		cmd = build_mpi_command_string(
			executable,
			executable_args=executable_args,
			np=info["np"],
			hosts=info.get("hosts", None),
			dvm_uri=dvm
		)

		return cmd

	def test(self):
		return "****Testing the TNQVM QRC****"
