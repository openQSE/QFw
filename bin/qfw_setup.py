import os, sys, logging,socket
import svc_launcher, cdefw_global
from defw_util import expand_host_list
from defw_exception import DEFwExecutionError
from defw_cmd import defw_exec_remote_cmd

def cleanup_dvm():
	try:
		pid = launcher.launch('pkill -9 srun')
		pid = launcher.launch('pkill -9 prted')
		pid = launcher.launch('pkill -9 prte')
	except:
		pass

def start_dvm(node_list, launcher):
	cmd = f'prte --host {",".join(node_list)}'
	try:
		pid = launcher.launch(cmd)
		logging.debug(f"{cmd} launched with pid: {pid}")
	except Exception as e:
		raise e

def start_resmgr(my_hostname, launcher):
	resmgr = f"resmgr_{my_hostname}"

	env =  {'DEFW_AGENT_NAME': resmgr,
			'DEFW_LISTEN_PORT': str(8090),
			'DEFW_ONLY_LOAD_MODULE': 'svc_resmgr',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': 'service',
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': resmgr,
			'DEFW_LOG_LEVEL': "all",
			'DEFW_DISABLE_RESMGR': "no",
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
							resmgr),
			'DEFW_PARENT_HOSTNAME': my_hostname}

	pid = launcher.launch('python3', env=env)
	logging.debug(f"ResMgr returned launched with pid: {pid}")

def start_launcher(target, resmgr):
	launcher = f"launcher_{target}"

	env =  {'DEFW_AGENT_NAME': launcher,
			'DEFW_LISTEN_PORT': str(8190),
			'DEFW_ONLY_LOAD_MODULE': 'svc_launcher',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': 'service',
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': resmgr,
			'DEFW_LOG_LEVEL': "all",
			'DEFW_DISABLE_RESMGR': "no",
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
							launcher),
			'DEFW_PARENT_HOSTNAME': resmgr}

	cmd = "; ".join([f"export {var_name}={var_value}" \
		for var_name, var_value in env.items()]) + "; python3"

	defw_exec_remote_cmd(cmd, target, deamonize=True)

	logging.debug(f"Launcher started")

def start_qpm(resmgr, node_list, launcher):
	qpm = f"qpm_{resmgr}"

	env =  {'DEFW_AGENT_NAME': qpm,
			'DEFW_LISTEN_PORT': str(8290),
			'DEFW_ONLY_LOAD_MODULE': 'svc_qpm,api_launcher,api_qrc',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': 'service',
			'DEFW_PARENT_HOSTNAME': resmgr,
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': resmgr,
			'DEFW_LOG_LEVEL': "all",
			'DEFW_DISABLE_RESMGR': "no",
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
								qpm),
			'QFW_BASE_QRC_PORT': str(9100),
			'QFW_NUM_QRC': str(1),
			'QFW_QRC_BIN_PATH': 'python3',
			'QFW_QPM_ASSIGNED_HOSTS': node_list,
		}

	pid = launcher.launch("python3", env=env)
	logging.debug(f"qpm started with pid {pid}")

def start_qtm(resmgr, node_list):
	head_node = node_list[0]

	qtm = f"qtm_{head_node}"

	env =  {'DEFW_AGENT_NAME': qtm,
			'DEFW_LISTEN_PORT': str(8390),
			'DEFW_ONLY_LOAD_MODULE': 'api_qpm',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': 'service',
			'DEFW_PARENT_HOSTNAME': resmgr,
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': resmgr,
			'DEFW_LOG_LEVEL': "all",
			'DEFW_DISABLE_RESMGR': "no",
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
							qtm),
		}

	cmd = "; ".join([f"export {var_name}={var_value}" \
		for var_name, var_value in env.items()]) + "; python3"

	defw_exec_remote_cmd(cmd, head_node)

	logging.debug(f"qtm returned output: {output}, error {error}, rc {rc}")

def extract_group_node_lists(env):
	info = env.split("\n")
	if len(info) != 2:
		raise DEFwExecutionError(f"Unexpected group parameters: {env}")

	node_lists = []
	for line in info:
		key, value = line.split('=', 1)
		pos = int(key.split("SLURM_JOB_NODELIST_HET_GROUP_")[1])
		nl = expand_host_list(value)
		node_lists.insert(pos, nl)

	return node_lists[0], node_lists[1]

if __name__ == '__main__':
	g0_node_list, g1_node_list = extract_group_node_lists(sys.argv[1])
	hostname = socket.gethostname()
	if hostname == g1_node_list[0]:
		# get a launcher object
		launcher = svc_launcher.Launcher()

		try:
			# Start PRTE DVM
			start_dvm(g1_node_list, launcher)
			logging.debug("DVM STARTED")

			# Start the Resource Manager
			start_resmgr(hostname, launcher)
			logging.debug("RESMGR STARTED")

			# Start the Launcher
			for node in g1_node_list:
				if node != hostname:
					break
			start_launcher(node, hostname)
			logging.debug("LAUNCHER STARTED")

			# Start the QPM
			start_qpm(hostname, ",".join(g1_node_list), launcher)
			logging.debug("QPM STARTED")

			# Start the QTM
			start_qtm(hostname, g0_node_list)
			logging.debug("QTM STARTED")
		except Exception as e:
			cleanup_dvm()
			launcher.shutdown()
			raise e
	else:
		print(f"I'm '{hostname}' not the head node '{g1_node_list[0]}'")


