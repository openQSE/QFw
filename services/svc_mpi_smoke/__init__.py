from .svc_mpi_smoke import MPISmoke
import json
import logging
import os
import threading
from pathlib import Path
from time import monotonic, sleep, time_ns

import defw

SERVICE_NAME = 'MPISmoke'
SERVICE_DESC = 'MPI-backed smoke test service for QFw'
SERVICE_READY_FILE_ENV = "QFW_SERVICE_READY_FILE"

svc_info = {
	'name': SERVICE_NAME,
	'module': __name__,
	'description': SERVICE_DESC,
	'version': 1.0,
	'instance_mode': 'singleton',
}

service_classes = [MPISmoke]

_registration_lock = threading.Lock()
_registration_records = []
_registration_thread = None


def _env_enabled(name, default=True):
	value = os.environ.get(name)
	if value is None:
		return default
	return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _startup_timeout():
	try:
		return int(os.environ.get("QFW_STARTUP_TIMEOUT", "60"))
	except ValueError:
		return 60


def _registration_context():
	service_id = (
		os.environ.get("QFW_SERVICE_ID") or
		os.environ.get("QFW_QPM_SERVICE_ID") or
		os.environ.get("DEFW_AGENT_NAME") or
		SERVICE_NAME
	)
	return {"service_id": service_id}


def _write_service_ready():
	path = os.environ.get(SERVICE_READY_FILE_ENV)
	if not path:
		return
	ready_file = Path(path)
	try:
		ready_file.parent.mkdir(parents=True, exist_ok=True)
		with ready_file.open("w", encoding="utf-8") as stream:
			json.dump({
				"ready": True,
				"message": "MPI smoke service registered",
				"timestamp_ns": time_ns(),
			}, stream, sort_keys=True)
			stream.write("\n")
	except OSError:
		logging.exception("failed to write MPI smoke readiness file")


def _register_with_dirsvc():
	deadline = monotonic() + max(0, _startup_timeout())
	while monotonic() < deadline:
		dirsvc = getattr(defw, "dirsvc", None)
		if dirsvc is None:
			sleep(0.2)
			continue
		try:
			records = dirsvc.register_service(
				defw.me.my_endpoint(),
				context=_registration_context(),
			)
			with _registration_lock:
				_registration_records[:] = list(records or [])
			_write_service_ready()
			logging.debug("MPI smoke service registered with dirsvc")
			return
		except Exception:
			logging.exception("failed to register MPI smoke service")
			sleep(1)
	logging.error("timed out waiting to register MPI smoke service")


def initialize():
	global _registration_thread

	if not _env_enabled("QFW_QPM_REGISTER_WITH_DIRSVC"):
		return None
	with _registration_lock:
		if _registration_thread is not None:
			return None
		_registration_thread = threading.Thread(target=_register_with_dirsvc)
		_registration_thread.daemon = True
		_registration_thread.start()
	return None


def uninitialize():
	with _registration_lock:
		records = list(_registration_records)
		_registration_records[:] = []
	dirsvc = getattr(defw, "dirsvc", None)
	if dirsvc is not None:
		for record in records:
			try:
				dirsvc.deregister_service(
					record["service_id"],
					record["runtime_id"],
					record["generation"],
				)
			except Exception:
				logging.exception("failed to deregister MPI smoke service")
	return None
