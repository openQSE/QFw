import json
import os
import sys

from defw_exception import DEFwError, DEFwExecutionError
from util.mpi import build_mpi_command_string
from util.qpm.util_qrc import UTIL_QRC

SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
AER_RANKS = 1
CIRCUIT_RUNNER = os.path.join(SERVICE_DIR, "circuit_runner.py")


class QRC(UTIL_QRC):
	def parse_result(self, out):
		lines = out.decode("utf-8").strip().splitlines()
		if not lines:
			raise DEFwError({"Error": "Empty output!"})
		try:
			return json.loads(lines[-1])["counts"]
		except (ValueError, KeyError) as e:
			raise DEFwError({"Error": f"Could not parse result: {e}"})

	def form_cmd(self, circ, qasm_file):
		info = circ.info
		aer = info["aer"]
		dvm = os.environ.get("QFW_DVM_URI_PATH", "").strip()
		if dvm and not os.path.exists(dvm):
			raise DEFwExecutionError(f"dvm-uri {dvm} doesn't exist")

		executable_args = [
			CIRCUIT_RUNNER,
			'--qasm-file', qasm_file,
			'--shots', info["num_shots"],
			'--mode', aer["mode"],
			'--device', aer["device"],
			'--method', aer["method"],
			'--max-memory-mb', aer["max_memory_mb"],
		]
		if aer.get("device_id"):
			executable_args.extend(['--device-id', aer["device_id"]])
		if aer.get("target_gpus"):
			executable_args.extend(['--target-gpus', *aer["target_gpus"]])
		for name in ("mps_max_bond_dimension", "mps_truncation_threshold"):
			if aer.get(name) is not None:
				executable_args.extend(['--' + name.replace('_', '-'), aer[name]])
		if info.get("seed_simulator") is not None:
			executable_args.extend(['--seed', info["seed_simulator"]])

		return build_mpi_command_string(
			sys.executable,
			executable_args=executable_args,
			np=AER_RANKS,
			hosts=info.get("hosts", None),
			dvm_uri=dvm
		)

	def test(self):
		return "****Testing the Aer QRC****"
