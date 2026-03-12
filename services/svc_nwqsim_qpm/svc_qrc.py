from defw_agent_info import *
import logging, sys, os
import importlib, yaml, psutil
from defw_exception import DEFwError, DEFwExecutionError
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
			lines = out_str.split("\n")
			catch = -1
			for i, each_line in enumerate(lines):
				if "===============  Measurement" in each_line:
					catch = i
			if catch == -1:
				raise DEFwError({"Error": "Could not parse result!"})
				return {"Error": "Could not parse result!"}
			results = lines[catch+1:-1]
			counts = {}
			for each_res_line in results:
				k,v = each_res_line.split(":")
				k = k.strip('" ').strip()
				v = int(v)
				counts[k] = v
			return counts
		except Exception as e:
			raise DEFwError({"Error": str(e)})
			return {"Error": str(e)}

	def form_cmd(self, circ, qasm_file):
		import shutil
		info = circ.info

		nwqsim_executable = shutil.which(info['qfw_backend'])
		gpuwrapper = shutil.which("gpuwrapper.sh")

		if not nwqsim_executable or not gpuwrapper:
			raise DEFwExecutionError("Couldn't find nwqsim_executable or gpuwrapper. Check paths")

		if not os.path.exists(info["qfw_dvm_uri_path"].split('file:')[1]):
			raise DEFwExecutionError(f"dvm-uri {info['qfw_dvm_uri_path']} doesn't exist")

		hosts = ''
		for k, v in info["hosts"].items():
			if hosts:
				hosts += ','
			hosts += f"{k}:{v}"

		try:
			dvm = info["qfw_dvm_uri_path"]
		except:
			dvm = "search"
		exec_cmd = shutil.which(info["exec"])

		cmd = f'{exec_cmd} --dvm {dvm} -x LD_LIBRARY_PATH ' \
			  f'--mca btl ^tcp,ofi,vader,openib ' \
			  f'--mca pml ^ucx --mca mtl ofi --mca opal_common_ofi_provider_include '\
			  f'{info["provider"]} --bind-to core '\
			  f'--np {info["np"]} --host {hosts} -v {nwqsim_executable} ' \
			  f'-q {qasm_file} '

		if "num_shots" in info:
			cmd += f' -shots {info["num_shots"]} '

		if "backend" in info:
			cmd += f'-backend {info["backend"]} '
		else:
			cmd += f'-backend OPENMP'

		if "method" in info:
			cmd += f' -sim {info["method"]}'

		return cmd

	def test(self):
		return "****Testing the NWQSIM QRC****"
