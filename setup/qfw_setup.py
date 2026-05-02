import subprocess, os, sys, logging, socket, traceback, \
		getopt, psutil, threading, yaml, shlex
from time import sleep, time
import svc_launcher, cdefw_global
from defw_util import expand_host_list, prformat, fg, bg
from defw_exception import DEFwExecutionError
from defw import me
from defw_cmd import defw_exec_remote_cmd

# TODO: we can use prun to run all the processes instead of using the python launcher

DEFAULT_SERVICES_CONFIG = 'qfw_services.yaml'

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

def runtime_mode():
	return os.environ.get('QFW_RUNTIME_MODE', 'cluster').strip().lower()

def in_container_mode():
	return runtime_mode() == 'container'

def qfw_tmp_dir():
	base_tmp = os.environ.get('QFW_TMP_PATH',
		os.path.join(os.environ['QFW_MASTER_SETUP_BASE_DIR'], 'tmp'))
	if 'QFW_RUN_TMP_PATH' in os.environ:
		return os.environ['QFW_RUN_TMP_PATH']
	if 'QFW_RUN_ID' in os.environ:
		return os.path.join(base_tmp, os.environ['QFW_RUN_ID'])
	current_path = os.path.join(base_tmp, 'current')
	if os.path.exists(current_path):
		with open(current_path, 'r') as f:
			run_id = f.readline().strip()
		if run_id:
			return os.path.join(base_tmp, run_id)
	return base_tmp

def execute_local_command(command, daemonize=False):
	if daemonize:
		process = subprocess.Popen(
			command,
			shell=True,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			start_new_session=True,
		)
		return 0, '', ''
	process = subprocess.Popen(
		command,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
	)
	stdout, stderr = process.communicate()
	return process.returncode, stdout, stderr

def get_external_defw_env():
	env = {}
	for key in ['DEFW_EXTERNAL_SERVICES_PATH',
				'DEFW_EXTERNAL_SERVICE_APIS_PATH']:
		if key in os.environ:
			env[key] = os.environ[key]
	return env

def start_qfw(host, hetgroups, services_config):
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

	qfw_activate = os.path.join(os.environ['QFW_SETUP_PATH'], 'qfw_activate')
	tmp_dir = qfw_tmp_dir()
	cmd = f"source {qfw_activate} >& {tmp_dir}/qfw_activate_result && "
	cmd += 'nohup qfw_run_setup.sh '
	cmd += f'{shlex.quote(hetgroups)} {shlex.quote(services_config)} '
	cmd += f'>& {tmp_dir}/qfw_run_setup.out'
	prformat(fg.cyan+fg.bold, f"Starting QFW: {cmd}")
	if in_container_mode() and host == socket.gethostname():
		execute_local_command(cmd, daemonize=True)
	else:
		defw_exec_remote_cmd(cmd, host, deamonize=True)

def start_dvm(node_list):
	try:
		job_id = os.environ['QFW_JOB_ID']
	except KeyError:
		job_id = -1
	host_list = ",".join(f"{node}:*" for node in node_list)
	if 'QFW_DVM_URI_PATH' in os.environ:
		uri = os.environ['QFW_DVM_URI_PATH']
	else:
		uri = os.path.join(os.path.split(cdefw_global.get_defw_tmp_dir())[0],
					 'prte_dvm', 'dvm-uri')
	qfw_activate = os.path.join(os.environ['QFW_SETUP_PATH'], 'qfw_activate')
	tmp_dir = qfw_tmp_dir()
	cmd = f"source {qfw_activate} >& {tmp_dir}/qfw_activate_for_prte && "
	if job_id != -1 and 'QFW_HET_GROUP' in os.environ:
		cmd += f'qfw_run_prte.sh {os.path.split(uri)[0]} "{host_list}" {job_id}'
	else:
		cmd += f'qfw_run_dev_prte.sh {os.path.split(uri)[0]} "{host_list}"'
	rc, out, err = execute_ssh_command(node_list[0], cmd)
	logging.debug(f"cmd = {cmd}; rc = {rc}; out: {out}; error: {err}")
	if rc:
		logging.critical(
			"DVM startup failed: cmd=%s rc=%s stdout=%s stderr=%s",
			cmd,
			rc,
			out,
			err,
		)
		raise DEFwExecutionError(f"Failed to start DVM. rc = {rc}")
	logging.info("DVM started on hosts %s using URI %s", host_list, uri)
	logging.debug("DVM startup output: stdout=%s stderr=%s", out, err)
	return rc

def start_resmgr(target, launcher, env_dict):
	resmgr = f"resmgr_{target}"
	tmp_path = qfw_tmp_dir()
	pref_path = os.path.join(tmp_path, 'defw_resmgr_pref.yaml')

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
			'DEFW_PREF_PATH': pref_path,
			'DEFW_PARENT_HOSTNAME': target}

	env.update(get_external_defw_env())
	env.update(env_dict)

	pid = launcher.launch('defwp -d', env=env)
	return pid

def resolve_config_path(config_path):
	if config_path:
		if os.path.isabs(config_path) or os.path.exists(config_path):
			return config_path
		return os.path.join(os.environ['QFW_SETUP_PATH'], config_path)
	return os.path.join(os.environ['QFW_SETUP_PATH'], DEFAULT_SERVICES_CONFIG)

def load_services_config(config_path):
	config_path = resolve_config_path(config_path)
	with open(config_path, 'r') as f:
		cy = yaml.load(f, Loader=yaml.FullLoader)
	if not cy or 'services' not in cy:
		raise DEFwExecutionError(f"No services defined in {config_path}")
	return cy['services']

def resolve_node_policy(policy, g0, g1):
	if not policy or policy == 'group1-head':
		return g1[0]
	if policy == 'group0-head':
		return g0[0]
	if policy == 'local':
		return socket.gethostname()
	return policy

def resolve_host_policy(policy, g0, g1):
	if not policy:
		return ''
	if policy == 'group1':
		return ",".join(g1)
	if policy == 'group0':
		return ",".join(g0)
	if policy == 'all':
		return ",".join(list(dict.fromkeys(g0 + g1)))
	return policy

def start_service(service, resmgr, g0, g1, launcher, env_dict,
				  listen_port, telnet_port):
	name = service.get('name', None)
	module = service.get('module', None)
	if not name or not module:
		raise DEFwExecutionError(f"Service needs name and module: {service}")

	target = resolve_node_policy(service.get('target', 'group1-head'), g0, g1)
	agent_name = service.get('name')
	load_modules = service.get('load-modules', module)

	env =  {'DEFW_AGENT_NAME': agent_name,
			'DEFW_LISTEN_PORT': str(service.get('listen-port', listen_port)),
			'DEFW_TELNET_PORT': str(service.get('telnet-port', telnet_port)),
			'DEFW_ONLY_LOAD_MODULE': load_modules,
			'DEFW_LOAD_NO_INIT': '',
			'DEFW_SHELL_TYPE': 'daemon',
			'DEFW_AGENT_TYPE': service.get('agent-type', 'service'),
			'DEFW_PARENT_HOSTNAME': resmgr,
			'DEFW_PARENT_PORT': str(8090),
			'DEFW_PARENT_NAME': service.get('parent-name', f"resmgr_{resmgr}"),
			'DEFW_LOG_LEVEL': service.get('log-level', "error"),
			'DEFW_DISABLE_RESMGR': "no",
			'DEFW_LOG_DIR': os.path.join(
				os.path.split(cdefw_global.get_defw_tmp_dir())[0],
				agent_name),
			'DEFW_PY_LOGLEVEL': 'debug,DEFW_ALL'}

	assigned_hosts = resolve_host_policy(service.get('assigned-hosts'), g0, g1)
	if assigned_hosts:
		env['QFW_SERVICE_ASSIGNED_HOSTS'] = assigned_hosts
		assigned_hosts_env = service.get('assigned-hosts-env', None)
		if assigned_hosts_env:
			env[assigned_hosts_env] = assigned_hosts

	if 'QFW_DVM_URI_PATH' in os.environ:
		env['QFW_DVM_URI_PATH'] = os.environ['QFW_DVM_URI_PATH']

	env.update(get_external_defw_env())
	env.update(env_dict)

	logging.info(
		"Starting service %s on %s with module %s",
		agent_name,
		target,
		load_modules,
	)
	logging.debug("Service %s environment: %s", agent_name, env)
	pid = launcher.launch('defwp -d', env=env, target=target)
	logging.debug(
		f"Service {name} STARTED: pid {pid} agent {agent_name} on {target}")
	return pid, env['DEFW_LOG_DIR']

def start_services(services_config, resmgr, g0, g1, launcher, env_dict):
	services = load_services_config(services_config)
	listen_port = 8290
	telnet_port = 8291
	pids = []
	dirs = []

	for service in services:
		pid, log_dir = start_service(service, resmgr, g0, g1, launcher,
									 env_dict, listen_port, telnet_port)
		pids.append(pid)
		dirs.append(log_dir)
		listen_port += 100
		telnet_port += 100

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

def get_service_proc(file_path):
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

def wait_for_service(proc):
	while proc.is_running():
		try:
			logging.debug(f"waiting on service {proc}")
			proc.wait(timeout=5)
		except psutil.TimeoutExpired as e:
			logging.debug(f"Expired because of timeout")
			continue
		except Exception as e:
			logging.debug(f"Exception waiting on service: {e}")
			break

def wait_for_service_completion(service_log_dirs):
	num_services = len(service_log_dirs)
	file_paths = []
	procs = []
	for d in service_log_dirs:
		file_paths.append(os.path.join(d, "pid"))
	try:
		wtimeout = os.environ['QFW_STARTUP_TIMEOUT']
	except:
		wtimeout = 40

	logging.debug(f"service file paths = {file_paths}")

	start_time = time()
	while (time() - start_time) <= wtimeout:
		# wait for all service processes to start
		for file_path in file_paths:
			proc = get_service_proc(file_path)
			if proc:
				procs.append(proc)
				logging.debug(f"Waiting for service {proc.pid}")
				file_paths.remove(file_path)
		if len(file_paths) == 0:
			break
		sleep(1)

	logging.debug(f"service procs= {procs} num_services = {num_services}")
	if len(procs) != num_services:
		raise DEFwExecutionError("Services did not start properly")

	threads = []
	for proc in procs:
		logging.debug(f"Starting thread to wait for service: {proc}")
		thread = threading.Thread(target=wait_for_service, args=(proc,))
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

def start(g0, g1, launcher, shutdown, dvm, env_dict, services_config):
	try:
		# TODO: As part of the shutdown we need to collect all artifacts if they
		# were in the /tmp directories
		if shutdown:
			#cleanup_system(list_combine(g0, g1))
			cleanup_system(g1)
			me.exit()

		if dvm:
			# Start PRTE DVM
			start_dvm(g1)
			logging.debug("DVM STARTED")
			me.exit()

		# Start the Resource Manager
		pid = start_resmgr(g1[0], launcher, env_dict)
		logging.debug(f"RESMGR STARTED: {pid}")

		# Start the configured services
		service_pids, service_log_dirs = start_services(services_config, g1[0],
						g0, g1, launcher, env_dict)

	except Exception as e:
		logging.critical(f"Hit an exception. Cleaning up system: {e}")
		print_stacktrace()
		cleanup_system(list_combine(g0, g1))
		if launcher:
			launcher.shutdown()
		raise e

	logging.debug("FINISHED QFW FRAMEWORK STARTUP")
	logging.debug("Wait until configured services exit");
	wait_for_service_completion(service_log_dirs)
	cleanup_system(list_combine(g0, g1))
	if launcher:
		launcher.shutdown()
	logging.debug("Job Finished")
	me.exit()

def parse_env_vars(env):
	env_list = env.split(',')
	env_dict = {}
	for e in env_list:
		l = e.split('=')
		env_dict[l[0]] = l[1]
	return env_dict

if __name__ == '__main__':
	logging.debug(f"Running on {socket.gethostname()} with args {sys.argv}")
	argv = sys.argv[1:]
	try:
		options, args = getopt.getopt(argv, "g:u:o:p:rdsh",
		 ["groups=", "use=", "mods=", "env=",
		  "services-config=", "prun", "dvm", "shutdown", "help"])
	except:
		prformat(fg.red+fg.bold, f"bad command line arguments")
		me.exit()

	groups = ''
	dvm = False
	use_path = ''
	modules = ''
	env_vars = ''
	shutdown = False
	prun = False
	services_config = os.path.join(os.environ['QFW_SETUP_PATH'],
								   DEFAULT_SERVICES_CONFIG)
	launcher = None
	for name, value in options:
			if name in ['-g', '--groups']:
				groups = value
			elif name in ['-u', '--use']:
				use_path = value
			elif name in ['-o', '--mods']:
				modules = value
			elif name in ['-p', '--env']:
				env_vars = value
			elif name == '--services-config':
				services_config = value
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
	if (not g0_node_list or not g1_node_list) and not dev_run:
		raise DEFwExecutionError(f"Unexpected group parameters: {groups}")
	hostname = socket.gethostname()

	if prun:
		start_qfw(g1_node_list[0], groups, services_config)
		me.exit()

	# only run on node 0
	if hostname != g1_node_list[0] and not dvm and not shutdown and not dev_run:
		prformat(fg.red+fg.bold, f"This operation needs to run on {g1_node_list[0]}")
		me.exit()

	env_dict = {}
	if env_vars:
		env_dict = parse_env_vars(env_vars)

	if hostname == g1_node_list[0] and not dvm and not shutdown:
		# get a launcher object
		launcher = svc_launcher.Launcher()
	start(g0_node_list, g1_node_list, launcher, shutdown, dvm, env_dict,
		services_config)
