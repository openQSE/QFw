import pathlib
import sys
import threading

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from svc_lib_qpm.drivers.base_driver import reservation_cache_key  # noqa: E402
from svc_lib_qpm.drivers.qdmi_driver import QdmiDriver  # noqa: E402
from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402


class Profile:
	provider = "iqm"
	requires = "a fake library"

	def __init__(self, open_backend):
		self.open_backend = open_backend

	def describe(self, access):
		return ""


def qdmi_driver(open_backend):
	driver = QdmiDriver({})
	driver._access = lambda credential=None: {}
	driver._profile = Profile(open_backend)
	return driver


def backend():
	return type("Backend", (), {"device": object()})()


def test_both_drivers_key_logins_alike():
	credential = {"url": "u", "user": "alice", "aws_access_key_id": "AKIA1"}
	other = dict(credential, aws_access_key_id="AKIA2")

	for driver in (QdmiDriver({}), QrmiDriver({})):
		assert driver._credential_cache_key(credential) != (
			driver._credential_cache_key(other))
		assert driver._credential_cache_key(None) == ("default",)
		assert driver._credential_cache_key({"reservation_id": 7}) == (
			reservation_cache_key(7))


def test_a_login_opens_one_session_however_many_threads_ask():
	opened = []

	def open_backend(access):
		opened.append(1)
		return backend()

	driver = qdmi_driver(open_backend)
	gate = threading.Barrier(4)

	def ask():
		gate.wait()
		driver._backend({"url": "a"})

	threads = [threading.Thread(target=ask) for _ in range(4)]
	for thread in threads:
		thread.start()
	for thread in threads:
		thread.join()

	assert len(opened) == 1


def test_one_login_opening_does_not_hold_up_another():
	first_opening = threading.Event()
	second_opened = threading.Event()

	def open_backend(access):
		if not first_opening.is_set():
			first_opening.set()
			assert second_opened.wait(5)
		else:
			second_opened.set()
		return backend()

	driver = qdmi_driver(open_backend)
	first = threading.Thread(target=lambda: driver._backend({"url": "a"}))
	first.start()
	assert first_opening.wait(5)
	driver._backend({"url": "b"})
	first.join(5)

	assert not first.is_alive()
	assert len(driver._backends) == 2


def test_an_ended_reservation_loses_its_session_and_lock():
	driver = qdmi_driver(lambda access: backend())
	driver._backend({"reservation_id": 7})

	assert driver.evict_reservation(7) is True

	assert reservation_cache_key(7) not in driver._backends
	assert reservation_cache_key(7) not in driver._sessions.locks
	assert driver.evict_reservation(7) is False
