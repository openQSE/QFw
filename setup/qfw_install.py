import yaml, sys, getopt, os, stat

def qfw_configure(cy):
	base_dir = cy.get('base-dir', None)
	module_path = cy.get('module-path', None)
	module_file = cy.get('module-file', None)
	python_activate = cy.get('python-venv-activate', None)

	if not base_dir or not python_activate:
		raise ValueError("Configuration file needs to specify: "
						 "base-dir and python-venv-activate entries")

	qfw_setup_dir = os.path.join(base_dir, 'QFw', 'setup')
	qfw_env = os.path.join(base_dir, 'QFw', 'environment')

	os.makedirs(qfw_setup_dir, exist_ok=True)

	qfw_activate = os.path.join(qfw_setup_dir, 'qfw_activate')

	with open(qfw_activate, 'w') as f:
		f.write('#!/usr/bin/env bash\n')
		f.write('# QFW activate (must be sourced)\n\n')

		f.write('if [[ -n "${_QFW_ACTIVE:-}" ]]; then\n')
		f.write('    echo "QFW environment already active"\n')
		f.write('    return 0\n')
		f.write('fi\n\n')

		f.write('export _QFW_ACTIVE=1\n')
		f.write('export _QFW_OLD_CMAKE_PREFIX_PATH="$CMAKE_PREFIX_PATH"\n')
		f.write('export _QFW_OLD_PE_PRODUCT_LIST="$PE_PRODUCT_LIST"\n')
		f.write('export _QFW_OLD_PKG_CONFIG_PATH="$PKG_CONFIG_PATH"\n')
		f.write('export _QFW_OLD_PE_PKGCONFIG_LIBS="$PE_PKGCONFIG_LIBS"\n')
		f.write('export _QFW_OLD_PATH="$PATH"\n')
		f.write('export _QFW_OLD_LD_LIBRARY_PATH="$LD_LIBRARY_PATH"\n')
		f.write('export _QFW_OLD_PYTHONPATH="$PYTHONPATH"\n')

		f.write(f'export QFW_MASTER_SETUP_BASE_DIR="{base_dir}"\n')

		if module_path:
			f.write('export _QFW_USED_MODULES=1\n')
			f.write(f'module use {module_path}\n')
			f.write(f'module load {module_file}\n')
		else:
			libfab_env = os.path.join(qfw_env, 'qfw_libfabric_env.sh')
			mpi_env = os.path.join(qfw_env, 'qfw_mpi_env.sh')
			qfw_vars = os.path.join(qfw_env, 'qfw_set_envvars.sh')
			dev_vars = os.path.join(qfw_env, 'qfw_dev_env.sh')

			libfab = cy.get('libfabric-install', None)
			mpi = cy.get('mpi-install', None)
			if not mpi:
				raise ValueError("MPI install path need to be specified")
			dev = cy.get('dev-install', None)
			# TODO: how do you handle other devices more generically? cuda?
			if not dev:
				raise ValueError("Need to install Device Library")

			f.write(f'export QFW_MASTER_SETUP_MPI_INSTALL="{mpi}"\n')
			f.write(f'export QFW_MASTER_SETUP_DEV_INSTALL="{dev}"\n')
			if libfab:
					f.write(f'export QFW_MASTER_SETUP_LIBFABRIC_INSTALL="{libfab}"\n')
					f.write(f'source {libfab_env}\n')
			f.write(f'source {dev_vars}\n')
			f.write(f'source {mpi_env}\n')
			f.write(f'source {qfw_vars}\n')

		f.write(f'export QFW_VENV_PATH={os.path.dirname(os.path.dirname(python_activate))}\n')
		f.write(f'source {python_activate}\n\n')

		f.write('qfw_deactivate() {\n')
		f.write('    if [[ -z "${_QFW_ACTIVE:-}" ]]; then\n')
		f.write('        return 0\n')
		f.write('    fi\n\n')

		if module_path:
			f.write(f'    module unload {module_file}\n')
			f.write(f'    module unuse {module_path}\n')

		f.write('    if declare -f deactivate >/dev/null 2>&1; then\n')
		f.write('        deactivate\n')
		f.write('    fi\n\n')

		f.write('    export CMAKE_PREFIX_PATH=$_QFW_OLD_CMAKE_PREFIX_PATH\n')
		f.write('    export PE_PRODUCT_LIST=$_QFW_OLD_PE_PRODUCT_LIST\n')
		f.write('    export PKG_CONFIG_PATH=$_QFW_OLD_PKG_CONFIG_PATH\n')
		f.write('    export PE_PKGCONFIG_LIBS=$_QFW_OLD_PE_PKGCONFIG_LIBS\n')
		f.write('    export PATH="$_QFW_OLD_PATH"\n')
		f.write('    export LD_LIBRARY_PATH="$_QFW_OLD_LD_LIBRARY_PATH"\n')
		f.write('    export PYTHONPATH="$_QFW_OLD_PYTHONPATH"\n\n')

		f.write('    unset _QFW_ACTIVE\n')
		f.write('    unset _QFW_USED_MODULES\n')
		f.write('    unset _QFW_OLD_PATH\n')
		f.write('    unset _QFW_OLD_LD_LIBRARY_PATH\n')
		f.write('    unset _QFW_OLD_PYTHONPATH\n')
		f.write('    unset QFW_MASTER_SETUP_BASE_DIR\n')
		f.write('    unset QFW_MASTER_SETUP_MPI_INSTALL\n')
		f.write('    unset QFW_MASTER_SETUP_LIBFABRIC_INSTALL\n\n')
		f.write('    unset QFW_VENV_PATH\n\n')

		f.write('    unset -f qfw_deactivate\n')
		f.write('}\n')

	os.chmod(qfw_activate, os.stat(qfw_activate).st_mode | stat.S_IXUSR)

def print_help():
	print("Usage:")
	print("   qfw_install.py -c <config.yaml>")

def main(argv):
	try:
		options, args = getopt.getopt(argv, "c:h",
				["config=", "help"])
	except Exception as e:
		print(f"Bad command line arguments {argv}")
		raise(e)

	config = None
	for name, value in options:
		if name in ['-c', '--config']:
			config = value
		elif name in ['-h', '--help']:
			print_help()
			exit(1)
		else:
			print(f"Unrecognized parameter: {name}")
			print_help()
			exit(1)

	if not config:
		print_help()
		exit(1)

	with open(config, 'r') as f:
		cy = yaml.load(f, Loader=yaml.FullLoader)
		qfw_configure(cy)

if __name__ == '__main__':
	argv = sys.argv[1:]
	main(argv)

