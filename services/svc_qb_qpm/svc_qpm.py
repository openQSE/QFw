import os
import logging
import yaml
import requests
import threading
import svc_launcher
import cdefw_global
from time import sleep
from .svc_qrc import QRC
from util.qpm.util_qpm import UTIL_QPM
from util.qpm.util_circuit import set_max_qubits_pp
from defw_exception import DEFwDumper, DEFwNotFound, DEFwOutOfResources
from defw_cmd import defw_exec_remote_cmd

QB_START_TIMEOUT = 40
MAX_VQPU_QUBITS = 15
MAX_VQPU_PPN = 1


def send_request(vqpu_url):
	url = f'{vqpu_url}/get'  # This is a public testing endpoint
	try:
		response = requests.get(url)
		return response.status_code
	except Exception:
		return -1


def wait_for_vqpu(url, host, config):
	elapsed = 0
	while True:
		rc = send_request(url)
		if rc != -1:
			break
		if elapsed >= QB_START_TIMEOUT:
			break
		logging.debug(f"wait_for_vqpu: {url}, {host}, {config}")
		sleep(1)
		elapsed += 1
		continue
	if rc != -1:
		config[host]['status'] = True
	else:
		config[host]['status'] = False


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		set_max_qubits_pp(MAX_VQPU_QUBITS)

		self.vqpu_hosts = []
		self.vqpu_cfgs = {}
		self.launcher = None
		super().__init__(QRC(start=start), max_ppn=MAX_VQPU_PPN, start=start)
		self.configure_device_profile(max_qubits=MAX_VQPU_QUBITS)
		if start:
			self.start_vqpus()

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC
		from api_qpm import QPMType, QPMCapability
		info = self.query_helper(
			QPMType.QPM_TYPE_QB | QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_STATEVECTOR,
			SERVICE_NAME, SERVICE_DESC)
		logging.debug(f"QB {SERVICE_DESC}: {info}")
		return info

	def prepare_circuit(self, info):
		if info['num_qubits'] > MAX_VQPU_QUBITS:
			raise DEFwOutOfResources(f"Max supported qubits {MAX_VQPU_QUBITS}. Requested {info['num_qubits']}")
		info['qfw_backend'] = 'circuit_runner.qb'
		return info

	def get_backend_info(self, lib=None, token=None):
		return {
			'backend': 'qb',
			'metadata_supported': False,
		}

	def get_device_info(self, lib=None, token=None):
		return {
			'backend': 'qb',
			'metadata_supported': False,
		}

	def get_dynamic_backend_info(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			'backend': 'qb',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_calibration_snapshot(self, calibration_set_id=None, lib=None,
				     token=None):
		return {
			'backend': 'qb',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_coupling_graph(self, calibration_set_id=None, lib=None,
			       token=None):
		return {
			'backend': 'qb',
			'calibration_set_id': calibration_set_id,
			'metadata_supported': False,
		}

	def get_last_job_timing(self, cid=None, lib=None, reservation_id=None,
				token=None):
		return {
			'backend': 'qb',
			'cid': cid,
			'metadata_supported': False,
		}

	def get_last_job_metadata(self, cid=None, lib=None, reservation_id=None,
				  token=None):
		return {
			'backend': 'qb',
			'cid': cid,
			'metadata_supported': False,
		}

	def start_vqpus(self):
		cfg_template = os.path.join(
			os.environ['QFW_BIN_PATH'], 'QB', 'cfg', 'remote_backends.yaml')
		with open(cfg_template, 'r') as f:
			self.qb_cfg = yaml.load(f, Loader=yaml.FullLoader)

		tmp_dir = cdefw_global.get_defw_tmp_dir()

		self.vqpu_hosts = []
		self.vqpu_cfgs = {}
		threads = []
		for k, v in self.free_hosts.items():
			vqpu_url = f'http://{k}:8081'
			self.qb_cfg['loopback']['url'] = vqpu_url
			host_fname = f'{k}_qb.yaml'
			self.vqpu_cfgs[k] = {}
			self.vqpu_cfgs[k]['cfg'] = os.path.join(tmp_dir, host_fname)
			with open(self.vqpu_cfgs[k]['cfg'], 'w') as f:
				f.write(yaml.dump(self.qb_cfg, Dumper=DEFwDumper, indent=2, sort_keys=True))
			self.launcher = svc_launcher.Launcher()
			self.vqpu_hosts.append(k)
			vqpu_cmd = os.path.join(os.environ['QFW_BIN_PATH'], 'vqpu.sh')
			#vqpu_cmd = 'VQPU_PORT=8081 ' + vqpu_cmd
			#module_path = os.environ['MODULEPATH']
			#env = {'VQPU_PORT': '8081', 'MODULEPATH': module_path}
			env = {'VQPU_PORT': '8081'}
			logging.debug(f"Starting vQPU on {k} with {self.vqpu_cfgs[k]['cfg']} with:\n\t{vqpu_cmd}\n\t{env}")
			self.launcher.launch(f"{vqpu_cmd}", env=env, target=k)
			#self.launcher.launch(f"{vqpu_cmd}", target=k)
			thread = threading.Thread(target=wait_for_vqpu, args=(vqpu_url, k, self.vqpu_cfgs,))
			threads.append(thread)
			thread.start()

		for thread in threads:
			logging.debug("Waiting on vqpu wait thread to finish")
			thread.join()

		for k, v in self.vqpu_cfgs.items():
			if not v['status']:
				self.launcher.shutdown()
				raise DEFwNotFound(f"Failed to start vQPU on {k}")

	def prepare_provider_submission(self, circuit):
		logging.debug(
			f"Running {circuit.get_cid()}\n{circuit.info}\n{self.vqpu_cfgs}")
		h = list(circuit.info['hosts'].keys())[0]
		circuit.info['vqpu_url'] = self.vqpu_cfgs[h]['cfg']
		return circuit

	def shutdown_provider(self):
		for host in self.vqpu_hosts:
			os.remove(self.vqpu_cfgs[host]['cfg'])
			logging.debug(f"Killing vqpu.sh on {host}")
			defw_exec_remote_cmd("pkill -9 vqpu.sh", host=host)
			#self.launcher.launch("pkill -9 vqpu.sh", target=host)
			logging.debug(f"Killing qcstack on {host}")
			defw_exec_remote_cmd("pkill -9 qcstack", host=host)
			#self.launcher.launch("pkill -9 qcstack", target=host)
			if self.launcher:
				self.launcher.shutdown()
		super().shutdown_provider()

	def test(self):
		return "****QB QPM Test Successful****"
