import os, sys, subprocess, sysconfig

def setup_qfw_symlinks():
	defw = os.path.join(os.environ['DEFW_PATH'], 'src', 'defwp')
	venv_path = os.path.join(os.environ['QFW_VENV_PATH'], 'bin')
#	libdir = sysconfig.get_config_var("LIBDIR")

	# append the correct python library
#	if libdir:
#		old_ld = os.environ.get("LD_LIBRARY_PATH", "")
#		os.environ["LD_LIBRARY_PATH"] = libdir + (":" + old_ld if old_ld else "")

	py_version = sys.version.split()[0].strip()
	rc = subprocess.run([defw, "--py-version"], capture_output=True, text=True)
	if rc.returncode != 0:
		raise ValueError(f"{rc.returncode}: {rc.stderr.strip()}:-- {os.environ['PATH']}:{sys.base_exec_prefix} --")
	defw_version = rc.stdout.strip()
	if py_version != defw_version:
		raise ValueError(f"VENV python version ({py_version}) mismatches with defw version ({defw_version})." \
						  "DEFW must be compiled with the same version as the venv python version")

	py= f"python{sys.version_info.major}.{sys.version_info.minor}"
	if os.path.exists(os.path.join(venv_path, "python_defw_orig")) or \
	   os.path.exists(os.path.join(venv_path, "python3_defw_orig")) or \
	   os.path.exists(os.path.join(venv_path, py+"_defw_orig")):
		   raise RuntimeError("System is in an unexpected state")

	try:
		os.replace(os.path.join(venv_path, "python"), os.path.join(venv_path, "python_defw_orig"))
		os.replace(os.path.join(venv_path, "python3"), os.path.join(venv_path, "python3_defw_orig"))
		os.replace(os.path.join(venv_path, py), os.path.join(venv_path, py+"_defw_orig"))
		os.symlink(defw, os.path.join(venv_path, "python"))
		os.symlink(defw, os.path.join(venv_path, "python3"))
		os.symlink(defw, os.path.join(venv_path, py))
	except Exception as e:
		print("Failed to configure system properly")
		raise e

#	print(f"export LD_LIBRARY_PATH={libdir}:$LD_LIBRARY_PATH")

def restore_symlinks():
	defw = os.path.join(os.environ['DEFW_PATH'], 'src', 'defwp')
	venv_path = os.path.join(os.environ['QFW_VENV_PATH'], 'bin')

	py= f"python{sys.version_info.major}.{sys.version_info.minor}"
	if not os.path.exists(os.path.join(venv_path, "python_defw_orig")) or \
	   not os.path.exists(os.path.join(venv_path, "python3_defw_orig")) or \
	   not os.path.exists(os.path.join(venv_path, py+"_defw_orig")):
		   raise RuntimeError("System is in an unexpected state")

	os.replace(os.path.join(venv_path, "python_defw_orig"), os.path.join(venv_path, "python"))
	os.replace(os.path.join(venv_path, "python3_defw_orig"), os.path.join(venv_path, "python3"))
	os.replace(os.path.join(venv_path, py+"_defw_orig"), os.path.join(venv_path, py))

