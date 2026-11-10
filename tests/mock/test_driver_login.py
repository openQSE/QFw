# The login both drivers open a provider resource with.

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from defw_exception import DEFwExecutionError  # noqa: E402
from svc_lib_qpm.drivers.login import resolve_login  # noqa: E402


@pytest.fixture(autouse=True)
def no_service_login(monkeypatch):
	monkeypatch.delenv("QFW_QC_URL", raising=False)
	monkeypatch.delenv("QFW_API_KEY", raising=False)


def entry_lookup(monkeypatch, entry):
	import util.device_access as device_access
	calls = []

	def resolve(**kwargs):
		calls.append(kwargs)
		return entry
	monkeypatch.setattr(device_access, "resolve_device_access", resolve)
	return calls


def lookup_unless_complete(login):
	return not (login["base_url"] and login["token"])


def test_a_credential_beats_the_environment_and_skips_the_lookup(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://env.example.org")
	monkeypatch.setenv("QFW_API_KEY", "env-token")
	calls = entry_lookup(monkeypatch, {})

	login = resolve_login(
		{"id": "q20"},
		{"url": "https://cred.example.org", "api_key": "cred-token"},
		"iqm", lookup_unless_complete, "failed")

	assert login["base_url"] == "https://cred.example.org"
	assert login["token"] == "cred-token"
	assert login["device_id"] == "q20"
	assert calls == []


def test_the_environment_fills_what_the_credential_leaves_out(monkeypatch):
	monkeypatch.setenv("QFW_QC_URL", "https://env.example.org")
	monkeypatch.setenv("QFW_API_KEY", "env-token")

	login = resolve_login(
		{}, {"token": "cred-token"}, "iqm", lookup_unless_complete, "failed")

	assert login["base_url"] == "https://env.example.org"
	assert login["token"] == "cred-token"


def test_the_device_access_entry_fills_the_rest(monkeypatch):
	calls = entry_lookup(monkeypatch, {
		"url": "https://entry.example.org", "api_key": "entry-token",
		"quantum_computer": "garnet", "service_crn": "crn:entry"})

	login = resolve_login(
		{"id": "q20"},
		{"user": "alice", "credential_hint": "hint", "service_crn": None},
		"ibm", lookup_unless_complete, "failed", extras=("service_crn",))

	assert login["base_url"] == "https://entry.example.org"
	assert login["token"] == "entry-token"
	assert login["provider_device_id"] == "garnet"
	assert login["service_crn"] == "crn:entry"
	assert calls == [{
		"provider": "ibm", "device_id": "q20", "user": "alice",
		"credential_hint": "hint", "credential_handle": None}]


def test_a_failed_lookup_is_reported_with_the_callers_words(monkeypatch):
	import util.device_access as device_access

	def refuse(**kwargs):
		raise RuntimeError("no entry")
	monkeypatch.setattr(device_access, "resolve_device_access", refuse)

	with pytest.raises(DEFwExecutionError, match="cannot log in: no entry"):
		resolve_login({}, {}, "iqm", lookup_unless_complete, "cannot log in")


def test_a_caller_that_never_looks_up_never_reads_the_entry(monkeypatch):
	calls = entry_lookup(monkeypatch, {"url": "https://entry.example.org"})

	login = resolve_login(
		{}, {}, "pasqal", lambda login: False, "failed")

	assert login["base_url"] is None
	assert calls == []


def test_the_pasqal_qdmi_profile_never_reads_the_entry(monkeypatch):
	from svc_lib_qpm.drivers.qdmi_profiles import PasqalQdmiProfile
	calls = entry_lookup(monkeypatch, {"url": "https://entry.example.org"})
	monkeypatch.setenv("QFW_QC_URL", "https://env.example.org")
	profile = PasqalQdmiProfile({
		"provider_device_id": "emu_free", "project_id": "device-project"})

	access = profile.access({"api_key": "cred-token", "project_id": "mine"})

	assert access["base_url"] == "https://env.example.org"
	assert access["token"] == "cred-token"
	assert access["custom1"] == "EMU_FREE"
	assert access["custom3"] == "mine"
	assert calls == []
