import os, sys, logging, socket, traceback, getopt
from time import sleep
import svc_launcher, cdefw_global
from defw_util import expand_host_list
from defw_exception import DEFwExecutionError
from defw_cmd import defw_exec_remote_cmd
from defw import me

def cleanup_system(targets):
	for target in targets:
		defw_exec_remote_cmd("pkill -9 prte", target, deamonize=True)
		defw_exec_remote_cmd("pkill -9 prted", target, deamonize=True)
		defw_exec_remote_cmd("rm -Rf /tmp/prte*", target, deamonize=True)
		defw_exec_remote_cmd("pkill -9 python3", target, deamonize=True)

def start_dvm(node_list, use, modules):
	# Start the DVM on the second node in the Simulation Environment
	# because currently MPI can not co-exist on the DVM's head node.
	# Our launcher will live on node 0
	cmd = ''
	for u in use.split(':'):
		cmd += f"module use {u};"
	for m in modules.split(':'):
		cmd += f"module load {m};"
	cmd += f'prte --host {",".join(node_list)}'
	print(f"Executing command {cmd}")
	out, err = defw_exec_remote_cmd(cmd, node_list[1], deamonize=True)
	logging.debug(f"out: {out}\nerror: {err}")

def run_cmd_on_target(exe, env, target):
	cmd = "; ".join([f"export {var_name}={var_value}" \
		for var_name, var_value in env.items()]) + f"; {exe}"
	defw_exec_remote_cmd(cmd, target, deamonize=True)

def start_resmgr(target, launcher):
	resmgr = f"resmgr_{target}"

	env =  {'DEFW_AGENT_NAME': resmgr,
			'DEFW_LISTEN_PORT': str(8090),
			'DEFW_TELNET_PORT': str(8091),
			'DEFW_ONLY_LOAD_MODULE': 'svc_resmgr',
			'DEFW_LOAD_NO_INIT': '',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': 'resmgr',
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': resmgr,
			'DEFW_LOG_LEVEL': "all",
			'DEFW_DISABLE_RESMGR': "no",
			'DEFW_LOG_DIR': os.path.join('/tmp', resmgr),
#			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
#							resmgr),
			'DEFW_PARENT_HOSTNAME': target}

	pid = launcher.launch('python3 -d', env=env)
	return pid

def start_launcher(target, resmgr, launcher):
	name = f"launcher_{target}"

	env =  {'DEFW_AGENT_NAME': name,
			'DEFW_LISTEN_PORT': str(8190),
			'DEFW_TELNET_PORT': str(8191),
			'DEFW_ONLY_LOAD_MODULE': 'svc_launcher',
			'DEFW_LOAD_NO_INIT': '',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': 'service',
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': resmgr,
			'DEFW_LOG_LEVEL': "all",
			'DEFW_DISABLE_RESMGR': "no",
#			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
#							name),
			'DEFW_LOG_DIR': os.path.join('/tmp', name),
			'DEFW_PARENT_HOSTNAME': resmgr}

	pid = launcher.launch('python3 -d', env=env)
	return pid

def start_qpm(resmgr, target, node_list, launcher):
	qpm = f"qpm_{resmgr}"

	env =  {'DEFW_AGENT_NAME': qpm,
			'DEFW_LISTEN_PORT': str(8290),
			'DEFW_TELNET_PORT': str(8291),
			'DEFW_ONLY_LOAD_MODULE': 'svc_qpm,api_launcher,api_qrc',
			'DEFW_LOAD_NO_INIT': '',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': 'service',
			'DEFW_PARENT_HOSTNAME': resmgr,
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': 'resmgr'+resmgr,
			'DEFW_LOG_LEVEL': "all",
			'DEFW_DISABLE_RESMGR': "no",
#			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
#								qpm),
			'DEFW_LOG_DIR': os.path.join('/tmp', qpm),
			'QFW_BASE_QRC_PORT': str(9100),
			'QFW_NUM_QRC': str(1),
			'QFW_QRC_BIN_PATH': 'python3 -d',
			'QFW_QPM_ASSIGNED_HOSTS': node_list,
		}

	pid = launcher.launch('python3 -d', env=env)
	return pid

def start_qtm(resmgr, head_node, launcher):
	qtm = f"qtm_{head_node}"

	env =  {'DEFW_AGENT_NAME': qtm,
			'DEFW_LISTEN_PORT': str(8390),
			'DEFW_TELNET_PORT': str(8391),
			'DEFW_ONLY_LOAD_MODULE': 'api_qpm',
			'DEFW_LOAD_NO_INIT': '',
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

	run_cmd_on_target("python3 -d", env, head_node)

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

def print_stacktrace():
	exception_list = traceback.format_stack()
	exception_list = exception_list[:-2]
	exception_list.extend(traceback.format_tb(sys.exc_info()[2]))
	exception_list.extend(traceback.format_exception_only(sys.exc_info()[0], sys.exc_info()[1]))
	stacktrace = exception_str = "Traceback (most recent call last):\n"
	stacktrace = "\n".join(exception_list)
	logging.critical(stacktrace)

if __name__ == '__main__':
	logging.debug(f"Running on {socket.gethostname()} with args {sys.argv}")
	argv = sys.argv[1:]
	try:
		options, args = getopt.getopt(argv, "g:u:o:dsh",
								["groups=", "use=", "mods=", "dvm", "shutdown", "help"])
	except:
		print("bad command line arguments")
		me.exit()

	groups = ''
	dvm = False
	use_path = ''
	modules = ''
	shutdown = False
	for name, value in options:
			if name in ['-g', '--groups']:
				groups = value
			elif name in ['-u', '--use']:
				use_path = value
			elif name in ['-o', '--mods']:
				modules = value
			elif name in ['-d', '--dvm']:
				dvm = True
			elif name in ['-s', '--shutdown']:
				shutdown = True
			else:
				print_help()
				me.exit()

	g0_node_list, g1_node_list = extract_group_node_lists(groups)
	hostname = socket.gethostname()
	if hostname == g1_node_list[0] and not dvm and not shutdown:
		# get a launcher object
		launcher = svc_launcher.Launcher()

#	out, err = defw_exec_remote_cmd("hostname", g1_node_list[0])
#	print(f"Command results =\nout: {out}\nerror: {err}")

#	out, err = defw_exec_remote_cmd("hostname", g1_node_list[1])
#	print(f"Command results =\nout: {out}\nerror: {err}")

	try:
		if shutdown:
			cleanup_system(g0_node_list+g1_node_list)
			me.exit()

		if dvm:
			# Start PRTE DVM
			start_dvm(g1_node_list, use_path, modules)
			logging.debug("DVM STARTED")
			me.exit()

		# only run on node 0
		if hostname != g1_node_list[0]:
			me.exit()

		# Start the Resource Manager
		pid = start_resmgr(g1_node_list[0], launcher)
		logging.debug(f"RESMGR STARTED: {pid}")

		# Start the Launcher
		pid = start_launcher(g1_node_list[0], g1_node_list[0], launcher)
		logging.debug(f"LAUNCHER STARTED: {pid}")

		# Start the QPM
		#pid = start_qpm(g1_node_list[0], g1_node_list[0],
		#				",".join(g1_node_list), launcher)
		#logging.debug(f"QPM STARTED: {pid}")

		# Start the QTM
		#start_qtm(g0_node_list[0], launcher)
		#logging.debug("QTM STARTED")
	except Exception as e:
		logging.critical(f"Hit an exception. Cleaning up system: {e}")
		print_stacktrace()
		cleanup_system(g0_node_list+g1_node_list)
		launcher.shutdown()
		raise e

	launcher.shutdown(keep=True)
	logging.debug("FINISHED QFW PROCESS STARTUP")
#	sleep(300)
