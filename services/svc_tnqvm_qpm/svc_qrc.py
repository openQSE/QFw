from defw_agent_info import *  # noqa: F401,F403
import logging
import sys
import os
import json
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
		gpuwrapper = shutil.which("gpuwrapper.sh")

		if not circuit_runner or not gpuwrapper:
			logging.debug(f"{os.environ['PATH']}")
			logging.debug(f"{os.environ['LD_LIBRARY_PATH']}")
			raise DEFwExecutionError("Couldn't find circuit_runner or gpuwrapper. Check paths")

		if not os.path.exists(info["qfw_dvm_uri_path"].split('file:')[1]):
			raise DEFwExecutionError(f"dvm-uri {info['qfw_dvm_uri_path']} doesn't exist")

		hosts = ''
		for k, v in info["hosts"].items():
			if hosts:
				hosts += ','
			hosts += f"{k}:{v}"

		try:
			dvm = info["qfw_dvm_uri_path"]
		except KeyError:
			dvm = "search"

		exec_cmd = shutil.which(info["exec"])

		cmd = (
			f'{exec_cmd} --dvm {dvm} -x LD_LIBRARY_PATH '
			f'--mca btl ^tcp,ofi,vader,openib '
			f'--mca pml ^ucx --mca mtl ofi --mca opal_common_ofi_provider_include '
			f'{info["provider"]} --map-by {info["mapping"]} --bind-to core '
			f'--np {info["np"]} --host {hosts} {gpuwrapper} -v {circuit_runner} '
			f'-q {qasm_file} -b {info["num_qubits"]} -s {info["num_shots"]} '
			f'-c {compiler}')

		return cmd

	def test(self):
		return "****Testing the TNQVM QRC****"
