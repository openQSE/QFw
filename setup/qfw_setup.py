import subprocess, os, sys, logging, socket, traceback, getopt, psutil
from time import sleep
import svc_launcher, cdefw_global
from defw_util import expand_host_list, prformat, fg, bg
from defw_exception import DEFwExecutionError
from defw import me
from defw_cmd import defw_exec_remote_cmd

# TODO: we can use prun to run all the processes instead of using the python launcher

def cleanup_system(targets):
	prformat(fg.red+fg.bold, f"Shutting down targets: {targets}")
	for target in targets:
		defw_exec_remote_cmd("pterm", target, deamonize=True)
		defw_exec_remote_cmd("pkill -9 prte", target, deamonize=True)
		defw_exec_remote_cmd("pkill -9 prted", target, deamonize=True)
		defw_exec_remote_cmd("rm -Rf /tmp/prte*", target, deamonize=True)
		defw_exec_remote_cmd("pkill -9 -f 'python3 -d -x'", target, deamonize=True)

def execute_ssh_command(host, command, daemonize=False):
	ssh_command = f"ssh {host} '{command}'"
	if daemonize:
		ssh_command += " &"
	prformat(fg.green+fg.bold, f"Running: {ssh_command}")
	try:
		process = subprocess.Popen(ssh_command, shell=True,
				stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
		stdout, stderr = process.communicate()
		return_code = process.returncode
		prformat(fg.red+fg.bold, "BACK FROM THE Popen")
		prformat(fg.red+fg.bold, f"Command return: {stdout}\n{stderr}\n{rc}\n-----------")
		return return_code, stdout, stderr
	except Exception as e:
		return -1, '', str(e)

def start_qfw(host, use, modules, hetgroups):
	name = 'qfw_base_setup'
	if 'QFW_DVM_URI_PATH' in os.environ:
		uri = os.environ['QFW_DVM_URI_PATH']
	else:
		uri = os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
					 'prte_dvm', 'dvm-uri')

	# Using mpirun or prun to spin up the QFw infrastructure seems to lead
	# to a situation where mpirun calls to run the application eventually
	# fail with 195 return code. After debugging it appears like:
	#	prun_common.c:defhandler() is being called setting the status to -61
	#		-61 = PMIX_ERR_LOST_CONNECTION
	#	Eventually: prun_common() fails when it checks the status here:
	#	/* if we lost connection to the server, then we are done */
	#	(PMIX_ERR_LOST_CONNECTION == rc || PMIX_ERR_UNREACH == rc) {
	#		print_current_time(date);
	#		fprintf(logging, "%s:%s:%d:%d\n", date, __FILE__, __LINE__, rc);
	#		goto DONE;
	#	}
	# To avoid this issue use defw_exec_remote_cmd() to startup the
	# infrastructure processes. This is done via paramiko
	#
	#cmd += f'mpirun -np 1 --dvm file:{uri} qfw_run_setup.sh "{hetgroups}"'
	#execute_ssh_command(host, cmd, daemonize=True)

	for u in use.split(':'):
		cmd = f"module use {u};"
	for m in modules.split(':'):
		cmd += f"module load {m};"
	cmd += f'nohup qfw_run_setup.sh "{hetgroups}" >& /tmp/qfw_run_setup.out'
	prformat(fg.cyan+fg.bold, f"Starting QFW: {cmd}")
	defw_exec_remote_cmd(cmd, host, deamonize=True)

def start_dvm(node_list, use, modules):
	# get the job id of the head node
	job_id = os.environ['SLURM_JOB_ID']
	host_list = ",".join(f"{node}:*" for node in node_list)
	if 'QFW_DVM_URI_PATH' in os.environ:
		uri = os.environ['QFW_DVM_URI_PATH']
	else:
		uri = os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
					 'prte_dvm', 'dvm-uri')
	cmd = ''
	for u in use.split(':'):
		cmd += f"module use {u};"
	for m in modules.split(':'):
		cmd += f"module load {m};"
	cmd += f'run_prte.sh {os.path.split(uri)[0]} "{host_list}" {job_id}'
	rc, out, err = execute_ssh_command(node_list[0], cmd)
	logging.debug(f"rc = {rc}\nout: {out}\nerror: {err}")
	#out, err = defw_exec_remote_cmd(cmd, node_list[0], deamonize=False)
	return

	cmd = f'export SLURM_JOBID={job_id};export SLURM_JOB_ID={job_id};'
	# Start the DVM on the second node in the Simulation Environment
	# because currently MPI can not co-exist on the DVM's head node.
	# Our launcher will live on node 0
	if 'QFW_DVM_URI_PATH' in os.environ:
		uri = os.environ['QFW_DVM_URI_PATH']
	else:
		uri = os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
					 'prte_dvm', 'dvm-uri')
	cmd += f'mkdir -p {os.path.split(uri)[0]};'
	for u in use.split(':'):
		cmd += f"module use {u};"
	for m in modules.split(':'):
		cmd += f"module load {m};"
	# The end goal of the below parameters is to ensure that
	# SLINGSHOT_VNIS are available for the MPI processes being run by this
	# DVM
	#
	# ras ^slurm: tells it to use the host information passed on the
	#			  command line instead of SLURM's information
	# -prtemca plm slurm: forces it to use SLURM as the process launcher. In
	#					  effect use srun
	# plm_slurm_args "--het-group 1": passes this to the srun arg list which
	#				is need to tell srun to run on a specific het group
	cmd += f'prte --host {",".join(f"{node}:*" for node in node_list)} ' \
		   f'--report-uri {uri} '										 \
		   f'-x SLURM_JOB_ID -x SLURM_JOB_ID '							 \
		   f'--prtemca ras ^slurm '										 \
		   f'--prtemca plm slurm '										 \
		   f'--prtemca plm_slurm_args "--het-group 1"'
	out, err = defw_exec_remote_cmd(cmd, node_list[0], deamonize=True)
	logging.debug(f"out: {out}\nerror: {err}")

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
#			'DEFW_LOG_DIR': os.path.join('/tmp', resmgr),
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
							resmgr),
			'DEFW_PARENT_HOSTNAME': target}

	pid = launcher.launch('python3 -d', env=env)
	return pid

def start_launcher(resmgr, target, launcher, listen_port,
				   muse, modules, python_env):
	name = f"launcher_{target}_{listen_port}"

	env =  {'DEFW_AGENT_NAME': name,
			'DEFW_LISTEN_PORT': str(listen_port),
			'DEFW_TELNET_PORT': str(listen_port+1),
			'DEFW_ONLY_LOAD_MODULE': 'svc_launcher',
			'DEFW_LOAD_NO_INIT': '',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': 'service',
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': resmgr,
			'DEFW_LOG_LEVEL': "all",
			'DEFW_DISABLE_RESMGR': "no",
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
							name),
#			'DEFW_LOG_DIR': os.path.join('/tmp', name),
			'DEFW_PARENT_HOSTNAME': resmgr}

	pid = launcher.launch('python3 -d', env=env, target=target,
			muse=muse, modules=modules, python_env=python_env)
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
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
								qpm),
#			'DEFW_LOG_DIR': os.path.join('/tmp', qpm),
			'QFW_BASE_QRC_PORT': str(9100),
			'QFW_NUM_QRC': str(1),
			'QFW_QRC_BIN_PATH': 'python3 -d',
			'QFW_QPM_ASSIGNED_HOSTS': node_list,
		}

	if 'QFW_DVM_URI_PATH' in os.environ:
		env['QFW_DVM_URI_PATH'] = os.environ['QFW_DVM_URI_PATH']

	pid = launcher.launch('python3 -d', env=env, target=target)
	return pid, env['DEFW_LOG_DIR']

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

	pid = launcher.launch("python3 -d", env=env, target=head_node)

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

def get_qpm_proc(file_path):
	proc = None
	if os.path.exists(file_path):
		with open(file_path, "r") as file:
			pid = int(file.read())
		try:
			proc = psutil.Process(pid)
		except:
			proc = None
			pass
	return proc

def wait_for_qpm_completion(qpm_log_dir):
	file_path = os.path.join(qpm_log_dir, "pid")
	proc = None
	retries = 0
	while True:
		if not proc:
			proc = get_qpm_proc(file_path)
			if not proc:
				if retries > 20:
					break
				logging.debug(f"Waiting for QPM {file_path} to get created")
				sleep(1)
				retries += 1
				continue
		logging.debug(f"Waiting for QPM {proc.pid}")
		rc = -666
		try:
			rc = proc.wait(timeout=5)
		except psutil.TimeoutExpired as e:
			logging.debug(f"Expired because of timeout")
			continue
		except Exception as e:
			logging.debug(f"Exception waiting on qpm: {e}")
		break
	logging.debug(f"QPM is done {rc}")

if __name__ == '__main__':
	logging.debug(f"Running on {socket.gethostname()} with args {sys.argv}")
	argv = sys.argv[1:]
	try:
		options, args = getopt.getopt(argv, "g:u:o:p:rdsh",
		 ["groups=", "use=", "mods=", "python-env=",
		  "prun", "dvm", "shutdown", "help"])
	except:
		prformat(fg.red+fg.bold, f"bad command line arguments")
		me.exit()

	groups = ''
	dvm = False
	use_path = ''
	modules = ''
	python_env = ''
	shutdown = False
	prun = False
	for name, value in options:
			if name in ['-g', '--groups']:
				groups = value
			elif name in ['-u', '--use']:
				use_path = value
			elif name in ['-o', '--mods']:
				modules = value
			elif name in ['-p', '--python-env']:
				python_env = value
			elif name in ['-d', '--dvm']:
				dvm = True
			elif name in ['-r', '--prun']:
				prun = True
			elif name in ['-s', '--shutdown']:
				shutdown = True
			else:
				prformat(fg.red+fg.bold, f"Unknown parameters {name}:{value}")
				me.exit()

	g0_node_list, g1_node_list = extract_group_node_lists(groups)
	hostname = socket.gethostname()

	if prun:
		start_qfw(g1_node_list[0], use_path, modules, groups)
		me.exit()

	# only run on node 0
	if hostname != g1_node_list[0] and not dvm and not shutdown:
		prformat(fg.red+fg.bold, f"This operation needs to run on {g1_node_list[0]}")
		me.exit()

	if hostname == g1_node_list[0] and not dvm and not shutdown:
		# get a launcher object
		launcher = svc_launcher.Launcher()

	try:
		# TODO: As part of the shutdown we need to collect all artifacts if they
		# were in the /tmp directories
		if shutdown:
			cleanup_system(g0_node_list+g1_node_list)
			me.exit()

		if dvm:
			# Start PRTE DVM
			start_dvm(g1_node_list, use_path, modules)
			logging.debug("DVM STARTED")
			me.exit()

		# Start the Resource Manager
		pid = start_resmgr(g1_node_list[0], launcher)
		logging.debug(f"RESMGR STARTED: {pid}")

		# Start one Launcher
		listen_port = 8190
		logging.debug(f"Starting launcher on {g1_node_list[0]}")
		pid = start_launcher(g1_node_list[0], g1_node_list[0], launcher, listen_port,
					use_path, modules, python_env)
		logging.debug(f"LAUNCHER STARTED: {pid}")
#		for node in g1_node_list:
#			logging.debug(f"Starting launcher on {node}")
#			pid = start_launcher(g1_node_list[0], node, launcher, listen_port,
#						use_path, modules, python_env)
#			listen_port += 2
#			logging.debug(f"LAUNCHER STARTED: {pid}")

		# Start the QPM
		qpm_pid, qpm_log_dir  = start_qpm(g1_node_list[0], g1_node_list[0],
						",".join(g1_node_list), launcher)
		logging.debug(f"QPM STARTED: {qpm_pid}")

		# Start the QTM
		#start_qtm(g0_node_list[0], launcher)
		#logging.debug("QTM STARTED")
	except Exception as e:
		logging.critical(f"Hit an exception. Cleaning up system: {e}")
		print_stacktrace()
		cleanup_system(g0_node_list+g1_node_list)
		launcher.shutdown()
		raise e

	logging.debug("FINISHED QFW PROCESS STARTUP")
	logging.debug("Wait until the QPM exits");
	wait_for_qpm_completion(qpm_log_dir)
	cleanup_system(g0_node_list+g1_node_list)
	launcher.shutdown()
	logging.debug("Job Finished")
