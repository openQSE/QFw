# QRMI reads its credentials from the process environment when a resource is
# constructed, so writing those variables and constructing have to be one
# step. The shim QRC runs a circuit per thread, and circuits from different
# reservations resolve different credentials, so two threads interleaving the
# halves would have one open a resource from the other's environment: another
# user's API key, silently, against the right resource id.
#
# These drive the real _qpu with a stand-in qrmi module, so they exercise the
# locking rather than describing it.

import os
import pathlib
import sys
import threading
import time
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402


PROBE = "QFW_TEST_RESOURCE_ENV_PROBE"


class _Tracker:
	# Counts how many threads are inside the env-plus-construct section at
	# once, and what each construction saw in the environment.
	def __init__(self):
		self.inside = 0
		self.high_water = 0
		self.seen = {}
		self.opened = 0
		self.lock = threading.Lock()

	def enter(self, value):
		with self.lock:
			self.inside += 1
			self.high_water = max(self.high_water, self.inside)
		os.environ[PROBE] = value
		# Long enough that an unserialized second thread would overwrite the
		# environment before this one constructs.
		time.sleep(0.02)

	def leave(self, thread_name):
		with self.lock:
			self.seen[thread_name] = os.environ.get(PROBE)
			self.opened += 1
			self.inside -= 1


def _driver(tracker):
	driver = QrmiDriver({
		"provider": "ibm",
		"resource-type": "IBMQiskitRuntimeService",
		"provider-device-id": "ibm_torino",
	})

	resource_type = types.SimpleNamespace(
		IBMQiskitRuntimeService="IBMQiskitRuntimeService")

	def quantum_resource(alias, resource_type_value):
		tracker.leave(threading.current_thread().name)
		return types.SimpleNamespace(alias=alias)

	driver._qrmi = types.SimpleNamespace(
		ResourceType=resource_type, QuantumResource=quantum_resource)
	driver._access = lambda credential=None: {
		"base_url": "https://example.org", "token": "tok"}
	driver._ensure_resource_env = (
		lambda type_name, alias, credential=None: tracker.enter(
			dict(credential or {}).get("api_key", "none")))
	return driver


def _run(driver, credentials):
	errors = []

	def work(credential):
		try:
			driver._qpu(credential=credential)
		except Exception as exc:  # pragma: no cover - surfaced by the assert
			errors.append(exc)

	threads = [
		threading.Thread(target=work, args=(credential,), name=name)
		for name, credential in credentials.items()
	]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()
	assert not errors, errors


def test_each_thread_opens_its_resource_with_its_own_environment(monkeypatch):
	monkeypatch.delenv(PROBE, raising=False)
	tracker = _Tracker()
	driver = _driver(tracker)
	credentials = {
		f"user-{index}": {"user": f"user-{index}", "api_key": f"key-{index}"}
		for index in range(4)
	}

	_run(driver, credentials)

	assert tracker.high_water == 1, (
		"two threads were inside the environment section at once")
	assert tracker.seen == {
		name: credential["api_key"]
		for name, credential in credentials.items()
	}
	monkeypatch.delenv(PROBE, raising=False)


def test_threads_sharing_a_credential_open_one_resource(monkeypatch):
	# The second thread through the lock finds the resource the first opened
	# rather than building another.
	monkeypatch.delenv(PROBE, raising=False)
	tracker = _Tracker()
	driver = _driver(tracker)
	credential = {"user": "alice", "api_key": "key-alice"}
	credentials = {f"alice-{index}": dict(credential) for index in range(4)}

	_run(driver, credentials)

	assert tracker.opened == 1
	assert len(driver._resource_objs) == 1
	monkeypatch.delenv(PROBE, raising=False)
