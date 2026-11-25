# Guards the Backline QPM (svc_backline_qpm). Besides QFw's quantum reservation,
# it grants the classical QEC resources a program asks for (controller, decoder
# and transport), matched against the site inventory, and starts one
# catalyst-executor per reservation to host them. These tests pin the three
# reserve paths (quantum-only from the qfw-slurm gateway, a classical request
# against an existing reservation, both in one call), the ownership check on a
# classical request, and that components and executors are freed on release,
# expiry and shutdown.
#
# Admission, the scheduler and the executor launcher are stood in for, so no
# qhw-admission or Catalyst install is needed.

import sys

import pytest

import util.qpm.util_qpm as util_qpm
from defw_exception import DEFwExecutionError
from tests.mock.fakes import FakeSchedulerContext
from tests.mock.test_fake_iqm_qpm import FakeAdmissionContext
from svc_backline_qpm import executors
from svc_backline_qpm.svc_qpm import BACKLINE_TARGET_ID, QPM
from svc_backline_qpm.svc_qrc import QRC
from util.qpm.controller import _clear_target_controllers_for_tests


INVENTORY = """
controller:
  - id: local-cpu-ctrl
    hardware: cpu
    device: null.qubit
    device_lib: librtd_null_qubit.so
    transports:
      - memcpy
coprocessor:
  - id: local-cpu-coproc
    hardware: cpu
    transports:
      - memcpy
decoder:
  - id: steane-cpu
    code: steane
    hardware: cpu
    symbol: steane_coprocessor
    lib: ${CATALYST_LIB}/libsteane_coprocessor_cpu.so
"""


class FakeLauncher:
	def __init__(self, fail=False):
		self.fail = fail
		self.started = []
		self.stopped = []

	def start(self, catalyst_lib, plugins):
		if self.fail:
			raise executors.ExecutorStartError("boom")
		self.started.append(list(plugins))
		return executors.Running(f"fake-host:{47000 + len(self.started)}",
					 None, "")

	def stop(self, running):
		self.stopped.append(running.address)


def _qpm(monkeypatch, tmp_path, launcher=None):
	_clear_target_controllers_for_tests()
	monkeypatch.setenv("QFW_QPM_ASSIGNED_HOSTS", "localhost:1")
	monkeypatch.setattr(util_qpm, "qpm_initialized", True)
	(tmp_path / "libsteane_coprocessor_cpu.so").touch()
	inventory = tmp_path / "inventory.yaml"
	inventory.write_text(INVENTORY, encoding="utf-8")
	monkeypatch.setenv("CATALYST_LIB", str(tmp_path))
	monkeypatch.setenv("QFW_BACKLINE_INVENTORY", str(inventory))
	return QPM(
		admission_context_factory=FakeAdmissionContext,
		scheduler_context_factory=FakeSchedulerContext,
		executor_launcher=launcher or FakeLauncher(),
	)


def _request(job="job-backline", **intent_overrides):
	intent = {
		"version": 1,
		"controller": {"role": "qpu_control", "kind": "cpu"},
		"coprocessors": [{"role": "qec_decoder", "kind": "cpu",
				  "decoder": "steane"}],
		"transport": {"preferred": "memcpy"},
		"qec": {"code": "steane"},
	}
	intent.update(intent_overrides)
	return {
		"owner": {"user": "backline-user"},
		"job_id": job,
		"scope_id": "allocation-1",
		"target_device_id": BACKLINE_TARGET_ID,
		"walltime_ns": 1_000_000_000,
		"task_class": {"qubit_count": 3, "depth": 3, "shots": 10},
		"resource_intent": intent,
	}


def test_reserve_returns_placement_and_get_reservation_carries_it(
		monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)

	decision = qpm.reserve(request=_request())

	assert decision["status"] == "accepted", decision
	placement = decision["placement"]
	assert placement["controller"]["device"] == {
		"name": "null.qubit", "wires": 3}
	assert placement["coprocessors"][0]["function"] == {
		"symbol": "steane_coprocessor",
		"lib_path": str(tmp_path / "libsteane_coprocessor_cpu.so"),
	}
	assert placement["transport"] == "memcpy"
	reservation = qpm.get_reservation(
		reservation_id=decision["reservation_id"])
	assert reservation["placement"] == placement
	# The intent is not forwarded into QFw's admission request.
	admission = qpm.controller.admission_context
	assert "resource_intent" not in admission.requests[-1]


def test_components_are_exclusive_until_release(monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	first = qpm.reserve(request=_request("job-a"))
	assert first["status"] == "accepted", first

	second = qpm.reserve(request=_request("job-b"))
	assert second["status"] == "rejected"
	assert second["reason"] == "capacity-exhausted"

	assert qpm.release(
		reservation_id=first["reservation_id"])["status"] == "accepted"
	assert "placement" not in qpm.get_reservation(
		reservation_id=first["reservation_id"])
	third = qpm.reserve(request=_request("job-c"))
	assert third["status"] == "accepted", third


def test_unsupported_intent_is_rejected_before_admission(
		monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	admission = qpm.controller.admission_context

	decision = qpm.reserve(
		request=_request(qec={"code": "surface_code"}))

	assert decision["status"] == "rejected"
	assert decision["reason"] == "unsupported-qec-code"
	assert admission.requests == []
	assert qpm.evaluate(request=_request(
		qec={"code": "surface_code"}))["reason"] == "unsupported-qec-code"


def test_query_advertises_backline_capabilities(monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	properties = qpm.query()["properties"]

	assert properties["provider"] == "backline"
	assert properties["qec_codes"] == ["steane"]
	assert properties["transports"] == ["memcpy"]
	assert properties["placement_version"] == 1
	# Its own capability bit, so a plain statevector lookup never finds a
	# QPM that refuses every circuit.
	from api_qpm_common import QPMCapability
	assert properties["qpm_capabilities"] == QPMCapability.QPM_CAP_QEC


def test_qrc_refuses_execution():
	qrc = QRC()
	with pytest.raises(DEFwExecutionError, match="client-executed"):
		qrc.sync_run(None)
	with pytest.raises(DEFwExecutionError, match="client-executed"):
		qrc.async_run(None)


def test_expired_reservation_frees_components(monkeypatch, tmp_path):
	# This QPM receives no execution calls, so the controller never closes
	# an expired reservation on its own; the QPM must check expires_at_ns.
	qpm = _qpm(monkeypatch, tmp_path)
	first = qpm.reserve(request=_request("job-a"))
	assert first["status"] == "accepted", first
	admission = qpm.controller.admission_context
	admission.reservations[first["reservation_id"]]["expires_at_ns"] = 1

	assert "placement" not in qpm.get_reservation(
		reservation_id=first["reservation_id"])
	second = qpm.reserve(request=_request("job-b"))
	assert second["status"] == "accepted", second


def test_admission_profile_uses_the_qpm_qubit_limit(monkeypatch, tmp_path):
	from svc_backline_qpm.svc_qpm import MAX_QUBITS
	qpm = _qpm(monkeypatch, tmp_path)
	profile = qpm.controller.admission_context.registered_profiles[-1]
	assert profile["max_qubits"] == MAX_QUBITS == 3


def _quantum_only(job="job-backline"):
	request = _request(job)
	del request["resource_intent"]
	return request


def _classical(rid, job="job-backline", user="backline-user", **intent):
	request = _request(job, **intent)
	request["owner"] = {"user": user}
	request["for_reservation"] = rid
	return request


def test_gateway_reservation_has_no_classical_resources(monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	decision = qpm.reserve(request=_quantum_only())
	assert decision["status"] == "accepted", decision
	assert "placement" not in decision
	assert "placement" not in qpm.get_reservation(
		reservation_id=decision["reservation_id"])
	assert qpm._launcher.started == []


def test_classical_request_attaches_placement_and_starts_executor(
		monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	rid = qpm.reserve(request=_quantum_only())["reservation_id"]

	decision = qpm.reserve(request=_classical(rid))

	assert decision["status"] == "accepted", decision
	assert decision["reservation_id"] == rid
	placement = decision["placement"]
	address = placement["controller"]["executor"]["address"]
	assert placement["coprocessors"][0]["executor"]["address"] == address
	assert qpm.get_reservation(reservation_id=rid)["placement"] == placement
	plugins = qpm._launcher.started[0]
	assert plugins[:2] == list(executors.RUNTIME_PLUGINS)
	assert "librtd_null_qubit.so" in plugins
	assert str(tmp_path / "libsteane_coprocessor_cpu.so") in plugins


def test_direct_reserve_starts_executor_and_release_stops_it(
		monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	decision = qpm.reserve(request=_request())
	address = decision["placement"]["controller"]["executor"]["address"]
	assert qpm.release(
		reservation_id=decision["reservation_id"])["status"] == "accepted"
	assert qpm._launcher.stopped == [address]


def test_classical_request_rejects_unknown_foreign_or_placed(
		monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	rid = qpm.reserve(request=_quantum_only())["reservation_id"]

	assert qpm.reserve(request=_classical(9999))["reason"] == "invalid-request"
	assert qpm.reserve(
		request=_classical(rid, user="someone-else"))["reason"] == "invalid-request"
	assert qpm.reserve(
		request=_classical(rid, job="other-job"))["reason"] == "invalid-request"
	assert qpm.reserve(request=_classical(rid))["status"] == "accepted"
	assert qpm.reserve(request=_classical(rid))["reason"] == "invalid-request"
	assert len(qpm._launcher.started) == 1


def test_classical_request_must_name_the_owner_and_job_it_records(
		monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	rid = qpm.reserve(request=_quantum_only())["reservation_id"]

	for field in ("owner", "job_id"):
		request = _classical(rid)
		del request[field]
		assert qpm.reserve(request=request)["reason"] == "invalid-request"
	assert qpm._launcher.started == []
	assert qpm.reserve(request=_classical(rid))["status"] == "accepted"


def test_executor_start_failure(monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path, launcher=FakeLauncher(fail=True))
	admission = qpm.controller.admission_context

	direct = qpm.reserve(request=_request())
	assert direct["reason"] == "executor-start-failed"
	assert all(r["state"] == "released" for r in admission.reservations.values())

	rid = qpm.reserve(request=_quantum_only("job-q"))["reservation_id"]
	classical = qpm.reserve(request=_classical(rid, job="job-q"))
	assert classical["reason"] == "executor-start-failed"
	assert "placement" not in qpm.get_reservation(reservation_id=rid)


def test_expiry_stops_the_executor(monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	first = qpm.reserve(request=_request("job-a"))
	address = first["placement"]["controller"]["executor"]["address"]
	qpm.controller.admission_context.reservations[
		first["reservation_id"]]["expires_at_ns"] = 1
	assert qpm.reserve(request=_request("job-b"))["status"] == "accepted"
	assert address in qpm._launcher.stopped


def test_shutdown_stops_all_executors(monkeypatch, tmp_path):
	qpm = _qpm(monkeypatch, tmp_path)
	address = qpm.reserve(
		request=_request())["placement"]["controller"]["executor"]["address"]
	qpm.shutdown_provider()
	assert qpm._launcher.stopped == [address]


def test_gpu_coprocessor_matched_when_intent_asks_for_gpu(
		monkeypatch, tmp_path):
	gpu = INVENTORY.replace(
		"decoder:\n", "  - id: local-gpu-coproc\n    hardware: gpu\n"
		"    transports:\n      - memcpy\ndecoder:\n  - id: steane-gpu\n"
		"    code: steane\n    hardware: gpu\n    symbol: gpu_steane_launcher\n"
		"    lib: ${CATALYST_LIB}/libsteane_coprocessor_cpu.so\n")
	monkeypatch.setattr(sys.modules[__name__], "INVENTORY", gpu)
	qpm = _qpm(monkeypatch, tmp_path)
	decision = qpm.reserve(request=_request(
		coprocessors=[{"role": "qec_decoder", "kind": "gpu", "decoder": "steane"}]))
	assert decision["status"] == "accepted", decision
	assert decision["placement"]["coprocessors"][0]["hardware"] == "gpu"


def test_inventory_controller_without_device_lib_is_refused_at_load(
		monkeypatch, tmp_path):
	monkeypatch.setattr(sys.modules[__name__], "INVENTORY", INVENTORY.replace(
		"    device_lib: librtd_null_qubit.so\n", ""))
	with pytest.raises(ValueError, match="device_lib"):
		_qpm(monkeypatch, tmp_path)
