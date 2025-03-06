import subprocess, os, sys, logging, socket, traceback, \
		getopt, psutil, threading, yaml
from time import sleep, time
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
		defw_exec_remote_cmd("pkill -6 -f  'defwp -d -x'", target, deamonize=True)

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
		#prformat(fg.red+fg.bold, "BACK FROM THE Popen")
		#prformat(fg.red+fg.bold, f"Command return: {stdout}\n{stderr}\n{rc}\n-----------")
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
	try:
		job_id = os.environ['QFW_JOB_ID']
	except:
		job_id = -1
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
	if job_id != -1 and 'QFW_HET_GROUP' in os.environ:
		cmd += f'qfw_run_prte.sh {os.path.split(uri)[0]} "{host_list}" {job_id}'
	else:
		cmd += f'qfw_run_dev_prte.sh {os.path.split(uri)[0]} "{host_list}"'
	rc, out, err = execute_ssh_command(node_list[0], cmd)
	logging.debug(f"rc = {rc}\nout: {out}\nerror: {err}")
	if rc:
		raise DEFwExecutionError(f"Failed to start DVM. rc = {rc}")
	return rc

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
			'DEFW_LOG_LEVEL': "error",
			'DEFW_DISABLE_RESMGR': "no",
#			'DEFW_LOG_DIR': os.path.join('/tmp', resmgr),
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
							resmgr),
			'DEFW_PARENT_HOSTNAME': target}

	pid = launcher.launch('defwp -d', env=env)
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
			'DEFW_LOG_LEVEL': "error",
			'DEFW_DISABLE_RESMGR': "no",
			'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
							name),
#			'DEFW_LOG_DIR': os.path.join('/tmp', name),
			'DEFW_PARENT_HOSTNAME': resmgr}

	pid = launcher.launch('defwp -d', env=env, target=target,
			muse=muse, modules=modules, python_env=python_env)
	return pid

def start_qpm(resmgr, target, node_list, launcher):
	with open(os.path.join(os.environ['QFW_SETUP_PATH'], 'qfw_qpm_services.yaml'), 'r') as f:
		cy = yaml.load(f, Loader=yaml.FullLoader)

	qpm_names = cy['QPM']

	listen_port = 8290
	telnet_port = 8291
	pids = []
	dirs = []

	for n in qpm_names:
		qpm = f"qpm_{n}_{resmgr}"

		env =  {'DEFW_AGENT_NAME': qpm,
				'DEFW_LISTEN_PORT': str(listen_port),
				'DEFW_TELNET_PORT': str(telnet_port),
				'DEFW_ONLY_LOAD_MODULE': f'svc_{n}_qpm,api_launcher',
				'DEFW_LOAD_NO_INIT': '',
				'DEFW_SHELL_TYPE': 'daemon',
				'DEFW_AGENT_TYPE': 'service',
				'DEFW_PARENT_HOSTNAME': resmgr,
				'DEFW_PARENT_PORT': str(8090),
				'DEFW_PARENT_NAME': 'resmgr'+resmgr,
				'DEFW_LOG_LEVEL': "error",
				'DEFW_DISABLE_RESMGR': "no",
				'DEFW_LOG_DIR': os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
									qpm),
	#			'DEFW_LOG_DIR': os.path.join('/tmp', qpm),
				'QFW_QPM_ASSIGNED_HOSTS': node_list,
			}

		if 'QFW_DVM_URI_PATH' in os.environ:
			env['QFW_DVM_URI_PATH'] = os.environ['QFW_DVM_URI_PATH']

		pid = launcher.launch('defwp -d', env=env, target=target)
		pids.append(pid)
		dirs.append(env['DEFW_LOG_DIR'])
		listen_port += 100
		telnet_port += 100

		logging.debug(f"QPM {qpm} STARTED: with pid {pid} on {target}")

	return pids, dirs

def extract_group_node_lists(env):
	if not env:
		return [], []
	info = env.split(":")
	if len(info) != 2:
		raise DEFwExecutionError(f"Unexpected group parameters: {env}")

	node_lists = []
	for line in info:
		key, value = line.split('=', 1)
		pos = int(key.split("GROUP_")[1])
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
			logging.debug(f"{file_path} - {pid}")
		try:
			proc = psutil.Process(pid)
		except Exception as e:
			logging.debug(e)
			proc = None
			pass
	return proc

def wait_for_qpm_service(proc):
	while proc.is_running():
		try:
			logging.debug(f"waiting on {proc}")
			proc.wait(timeout=5)
		except psutil.TimeoutExpired as e:
			logging.debug(f"Expired because of timeout")
			continue
		except Exception as e:
			logging.debug(f"Exception waiting on qpm: {e}")
			break

def wait_for_qpm_completion(qpm_log_dirs):
	num_qpms = len(qpm_log_dirs)
	file_paths = []
	procs = []
	for d in qpm_log_dirs:
		file_paths.append(os.path.join(d, "pid"))
	try:
		wtimeout = os.environ['QFW_STARTUP_TIMEOUT']
	except:
		wtimeout = 40

	logging.debug(f"qpm file paths = {file_paths}")

	start_time = time()
	while (time() - start_time) <= wtimeout:
		# wait for all the QPM processes to start
		for file_path in file_paths:
			proc = get_qpm_proc(file_path)
			if proc:
				procs.append(proc)
				logging.debug(f"Waiting for QPM {proc.pid}")
				file_paths.remove(file_path)
		if len(file_paths) == 0:
			break
		sleep(1)

	logging.debug(f"qpm procs= {procs} num_qpms = {num_qpms}")
	if len(procs) != num_qpms:
		raise DEFwExecutionError("QPM services did not start properly")

	threads = []
	for proc in procs:
		logging.debug(f"Starting thread to wait for qpm: {proc}")
		thread = threading.Thread(target=wait_for_qpm_service, args=(proc,))
		threads.append(thread)
		thread.start()

	for thread in threads:
		logging.debug("Waiting on thread to finish")
		thread.join()

def list_combine(l1, l2):
	if len(l1) == 0:
		return l2
	if len(l2) == 0:
		return l1
	for l in l2:
		if l not in l1:
			l1.append(l)
	return l1

def start(g0, g1, launcher, shutdown, dvm):
	try:
		# TODO: As part of the shutdown we need to collect all artifacts if they
		# were in the /tmp directories
		if shutdown:
			#cleanup_system(list_combine(g0, g1))
			cleanup_system(g1)
			me.exit()

		if dvm:
			# Start PRTE DVM
			start_dvm(g1, use_path, modules)
			logging.debug("DVM STARTED")
			me.exit()

		# Start the Resource Manager
		pid = start_resmgr(g1[0], launcher)
		logging.debug(f"RESMGR STARTED: {pid}")

		# Start one Launcher
		#listen_port = 8190
		#logging.debug(f"Starting launcher on {g1[0]}")
		#pid = start_launcher(g1[0], g1[0], launcher, listen_port,
		#			use_path, modules, python_env)
		#logging.debug(f"LAUNCHER STARTED: {pid}")

		# Start the QPM
		qpm_pid, qpm_log_dirs  = start_qpm(g1[0], g1[0],
						",".join(g1), launcher)

	except Exception as e:
		logging.critical(f"Hit an exception. Cleaning up system: {e}")
		print_stacktrace()
		cleanup_system(list_combine(g0, g1))
		if launcher:
			launcher.shutdown()
		raise e

	logging.debug("FINISHED QFW FRAMEWORK STARTUP")
	logging.debug("Wait until the QPM exits");
	wait_for_qpm_completion(qpm_log_dirs)
	cleanup_system(list_combine(g0, g1))
	if launcher:
		launcher.shutdown()
	logging.debug("Job Finished")
	me.exit()

if __name__ == '__main__':
	logging.debug(f"Running on {socket.gethostname()} with args {sys.argv}")
	argv = sys.argv[1:]
	try:
		options, args = getopt.getopt(argv, "g:u:o:p:rdsxh",
		 ["groups=", "use=", "mods=", "python-env=",
		  "prun", "dvm", "shutdown", "dev-run", "help"])
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
	dev_run = False
	launcher = None
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
			elif name in ['x', '--dev-run']:
				dev_run = True
			else:
				prformat(fg.red+fg.bold, f"Unknown parameters {name}:{value}")
				me.exit()

	g0_node_list, g1_node_list = extract_group_node_lists(groups)
	if not g0_node_list or not g1_node_list:
		raise DEFwExecutionError(f"Unexpected group parameters: {groups}")
	hostname = socket.gethostname()

	if prun:
		start_qfw(g1_node_list[0], use_path, modules, groups)
		me.exit()

	# only run on node 0
	if hostname != g1_node_list[0] and not dvm and not shutdown:
		prformat(fg.red+fg.bold, f"This operation needs to run on {g1_node_list[0]}")
		me.exit()

	if dev_run:
		launcher = svc_launcher.Launcher()
		start(g0_node_list, g1_node_list, launcher, False, None)
	else:
		if hostname == g1_node_list[0] and not dvm and not shutdown:
			# get a launcher object
			launcher = svc_launcher.Launcher()
		start(g0_node_list, g1_node_list, launcher, shutdown, dvm)

