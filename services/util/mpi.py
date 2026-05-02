import os
import shlex

import yaml


def _qfw_path():
	return os.environ.get(
		'QFW_PATH',
		os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
	)


def _resolve_qfw_path(path):
	path = os.path.expanduser(str(path))
	if os.path.isabs(path):
		return path
	return os.path.join(_qfw_path(), path)


def runtime_config_path():
	config_path = os.environ.get('QFW_SERVICE_RUNTIME_CONFIG', '').strip()
	if config_path:
		return _resolve_qfw_path(config_path)

	runtime_mode = os.environ.get('QFW_RUNTIME_MODE', 'cluster').strip().lower()
	return os.path.join(_qfw_path(), 'services', 'config', f'{runtime_mode}.yaml')


def load_runtime_config(config_path=None):
	if config_path is None:
		config_path = runtime_config_path()

	with open(config_path, 'r', encoding='utf-8') as stream:
		config = yaml.load(stream, Loader=yaml.FullLoader)

	return config or {}


def mpi_launch_config(config=None):
	if config is None:
		config = load_runtime_config()
	return config.get('mpi-launch', {}) or {}


def backend_config(backend, config=None):
	if config is None:
		config = load_runtime_config()
	return (config.get('backends', {}) or {}).get(backend, {}) or {}


def backend_wrapper(backend, config=None):
	return backend_config(backend, config).get('wrapper', None)


def _as_list(value):
	if value is None:
		return []
	if isinstance(value, (list, tuple)):
		return [str(item) for item in value if item is not None]
	return [str(value)]


def _normalize_dvm_uri(dvm_uri):
	if not dvm_uri:
		dvm_uri = os.environ.get('QFW_DVM_URI_PATH', '').strip()
	if not dvm_uri:
		return None
	if str(dvm_uri).startswith('file:'):
		return str(dvm_uri)
	return f'file:{dvm_uri}'


def format_hosts(hosts):
	if not hosts:
		return None
	if isinstance(hosts, dict):
		return ','.join(f'{host}:{slots}' for host, slots in hosts.items())
	if isinstance(hosts, (list, tuple)):
		return ','.join(str(host) for host in hosts)
	return str(hosts)


def _should_allow_run_as_root(value):
	if isinstance(value, bool):
		return value
	if value is None:
		value = 'auto'
	value = str(value).strip().lower()
	if value == 'auto':
		return os.geteuid() == 0
	return value in ('1', 'yes', 'true', 'on')


def build_mpi_command(executable, executable_args=None, np=1, hosts=None,
					  dvm_uri=None, config=None, launcher=None,
					  extra_mpi_args=None):
	mpi_config = mpi_launch_config(config)
	launcher = launcher or mpi_config.get('launcher', 'mpirun')
	cmd = [str(launcher)]

	if _should_allow_run_as_root(mpi_config.get('allow-run-as-root', 'auto')):
		cmd.append('--allow-run-as-root')

	dvm_uri = _normalize_dvm_uri(dvm_uri)
	if dvm_uri:
		cmd.extend(['--dvm', dvm_uri])

	for env_name in _as_list(mpi_config.get('export-env', [])):
		cmd.extend(['-x', env_name])

	for key, value in (mpi_config.get('mca', {}) or {}).items():
		if value is None:
			continue
		cmd.extend(['--mca', str(key), str(value)])

	map_by = mpi_config.get('map-by', None)
	if map_by:
		cmd.extend(['--map-by', str(map_by)])

	bind_to = mpi_config.get('bind-to', None)
	if bind_to:
		cmd.extend(['--bind-to', str(bind_to)])

	cmd.extend(_as_list(mpi_config.get('extra-args', [])))
	cmd.extend(_as_list(extra_mpi_args))
	cmd.extend(['--np', str(np)])

	host_arg = format_hosts(hosts)
	if host_arg:
		cmd.extend(['--host', host_arg])

	cmd.append(str(executable))
	cmd.extend(_as_list(executable_args))
	return cmd


def build_mpi_command_string(*args, **kwargs):
	return shlex.join(build_mpi_command(*args, **kwargs))
