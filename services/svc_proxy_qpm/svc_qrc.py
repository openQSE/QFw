import base64
import json
import logging
import os
import signal
import sys
import threading

import cdefw_global
from defw_exception import DEFwError, DEFwExecutionError
from util.circuit_payload import QPY, circuit_payload, logical_circuit_payload
from util.mpi import build_mpi_command_string, mpi_launch_config
from util.qpm.util_qrc import UTIL_QRC

PROXY_BACKEND_KIND = "proxy"
SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
CIRCUIT_RUNNER = os.path.join(SERVICE_DIR, "circuit_runner.py")
PROXY_RANKS = 1
LOG_SUFFIX = ".log"


def envelope(fmt, data):
	if fmt == QPY:
		return {"format": QPY, "data": base64.b64encode(data).decode("ascii")}
	return {"format": fmt, "data": data}


def runner_job(info):
	logical = logical_circuit_payload(info)
	return {
		"circuit": envelope(*circuit_payload(info)),
		"logical_circuit": None if logical is None else envelope(*logical),
		"bases": info.get("bases"),
		"num_shots": int(info["num_shots"]),
		"seed_simulator": info.get("seed_simulator"),
	}


def unbound(mpi_launch):
	return [] if mpi_launch.get("bind-to") else ["--bind-to", "none"]


def last_json_line(text):
	for line in reversed(text.strip().splitlines()):
		try:
			return json.loads(line)
		except ValueError:
			continue
	return None


class QRC(UTIL_QRC):
	def __init__(self, start=True):
		self._cancelled = set()
		self._cancel_lock = threading.Lock()
		super().__init__(start=start)

	def write_circuit_file(self, circ):
		job = json.dumps(runner_job(circ.info))
		job_file = os.path.join(cdefw_global.get_defw_tmp_dir(), f"{circ.get_cid()}.json")
		with open(job_file, "w", encoding="utf-8") as stream:
			stream.write(job)
		return job_file

	def form_cmd(self, circ, job_file):
		proxy = circ.info["proxy"]
		dvm = os.environ.get("QFW_DVM_URI_PATH", "").strip()
		if dvm and not os.path.exists(dvm):
			raise DEFwExecutionError(f"dvm-uri {dvm} doesn't exist")
		return build_mpi_command_string(
			sys.executable,
			executable_args=[
				CIRCUIT_RUNNER,
				"--job-file", job_file,
				"--log-file", job_file + LOG_SUFFIX,
				"--device-id", proxy["device_id"],
				"--max-memory-mb", proxy["max_memory_mb"],
			],
			np=PROXY_RANKS,
			hosts=circ.info.get("hosts", None),
			dvm_uri=dvm,
			extra_mpi_args=unbound(mpi_launch_config()),
		)

	def parse_result(self, out):
		result = last_json_line(out.decode("utf-8"))
		if not isinstance(result, dict) or "counts" not in result:
			raise DEFwError({"Error": "the proxy runner printed no result"})
		return result

	def parse_task_result(self, stdout, circ, task_info):
		self._relay_runner_log(task_info)
		return self.parse_result(stdout)

	def runner_failure_output(self, task_info, res, stdout):
		self._relay_runner_log(task_info)
		failure = last_json_line(stdout)
		if task_info.get("time_limit_hit") or not isinstance(failure, dict) or PROXY_BACKEND_KIND not in failure:
			return super().runner_failure_output(task_info, res, stdout)
		return failure

	def cleanup_task(self, circ, task_info):
		with self._cancel_lock:
			self._cancelled.discard(circ.get_cid())
		try:
			os.remove(task_info["qasm_file"] + LOG_SUFFIX)
		except (KeyError, OSError):
			pass

	def _relay_runner_log(self, task_info):
		try:
			with open(task_info["qasm_file"] + LOG_SUFFIX, encoding="utf-8") as stream:
				lines = stream.read().splitlines()
		except (KeyError, OSError):
			return
		for line in lines:
			logging.warning(f"proxy runner: {line}")

	def run_circuit_async(self, circ):
		if self._is_cancelled(circ.get_cid()):
			raise DEFwExecutionError("the job was cancelled before its runner started")
		task_info = super().run_circuit_async(circ)
		if self._is_cancelled(circ.get_cid()):
			self._kill(task_info)
		return task_info

	def cancel(self, provider_handle):
		with self._cancel_lock:
			self._cancelled.add(provider_handle)
		with self.worker_pool_lock:
			tasks = [task for worker in self.worker_pool for task in list(worker["active_tasks"])]
		for task_info in tasks:
			if task_info["circ"].get_cid() == provider_handle:
				self._kill(task_info)
		return "cancelled"

	def _is_cancelled(self, cid):
		with self._cancel_lock:
			return cid in self._cancelled

	def _kill(self, task_info):
		pid = task_info.get("pid")
		if not isinstance(pid, int) or pid <= 0:
			return
		task_info["cancelled"] = True
		try:
			os.killpg(pid, signal.SIGKILL)
		except ProcessLookupError:
			pass

	def test(self):
		return "****Testing the proxy QRC****"
