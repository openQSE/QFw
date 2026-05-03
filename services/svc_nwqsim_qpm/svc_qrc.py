from defw_agent_info import *  # noqa: F401,F403
import sys
import os
import numpy as np
from defw_exception import DEFwError, DEFwExecutionError
from util.mpi import backend_wrapper, build_mpi_command_string
from util.qpm.util_qrc import UTIL_QRC
from util.qpm.statevector import QFwStatevector

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
			lines = out_str.split("\n")
			catch = -1
			for i, each_line in enumerate(lines):
				if "===============  Measurement" in each_line:
					catch = i
			if catch == -1:
				raise DEFwError({"Error": "Could not parse result!"})
				return {"Error": "Could not parse result!"}
			results = lines[catch + 1:-1]
			counts = {}
			for each_res_line in results:
				k, v = each_res_line.split(":")
				k = k.strip('" ').strip()
				v = int(v)
				counts[k] = v
			return counts
		except Exception as e:
			raise DEFwError({"Error": str(e)})
			return {"Error": str(e)}

	def parse_task_result(self, out, circ, task_info):
		counts = self.parse_result(out)
		info = circ.info
		if not info.get("return_statevector", False):
			return counts

		dump_file = info.get("_qfw_statevector_dump", None)
		if not dump_file or not os.path.exists(dump_file):
			raise DEFwError({"Error": "Statevector dump file was not created"})

		statevector = self.parse_statevector_dump(
			dump_file, info.get("num_qubits", None))
		return {
			"counts": counts,
			"statevector": statevector.to_dict()
		}

	def parse_statevector_dump(self, dump_file, num_qubits):
		amplitudes = np.fromfile(dump_file, dtype=np.complex128)
		return QFwStatevector.from_complex_sequence(
			amplitudes, num_qubits=num_qubits, source="nwqsim")

	def cleanup_task(self, circ, task_info):
		dump_file = circ.info.get("_qfw_statevector_dump", None)
		if dump_file:
			try:
				os.remove(dump_file)
			except OSError:
				pass

	def form_cmd(self, circ, qasm_file):
		import shutil
		info = circ.info

		nwqsim_executable = shutil.which(info['qfw_backend'])

		if not nwqsim_executable:
			raise DEFwExecutionError("Couldn't find nwqsim_executable. Check paths")

		dvm = os.environ.get("QFW_DVM_URI_PATH", "").strip()
		if dvm and not os.path.exists(dvm):
			raise DEFwExecutionError(f"dvm-uri {dvm} doesn't exist")

		executable = nwqsim_executable
		executable_args = []
		wrapper = backend_wrapper('nwqsim')
		if wrapper:
			executable = shutil.which(wrapper)
			if not executable:
				raise DEFwExecutionError(f"Couldn't find {wrapper}. Check paths")
			executable_args.extend(['-v', nwqsim_executable])

		executable_args.extend(['-q', qasm_file])

		if "num_shots" in info:
			executable_args.extend(['-shots', info["num_shots"]])

		if "backend" in info:
			executable_args.extend(['-backend', info["backend"]])
		else:
			executable_args.extend(['-backend', 'OPENMP'])

		if "method" in info:
			executable_args.extend(['-sim', info["method"]])

		if info.get("return_statevector", False):
			dump_file = os.path.splitext(qasm_file)[0] + ".dump"
			info["_qfw_statevector_dump"] = dump_file
			executable_args.extend(['--dump_file', dump_file])

		cmd = build_mpi_command_string(
			executable,
			executable_args=executable_args,
			np=info["np"],
			hosts=info.get("hosts", None),
			dvm_uri=dvm
		)

		return cmd

	def test(self):
		return "****Testing the NWQSIM QRC****"
