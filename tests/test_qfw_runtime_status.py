import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))

from qfw_runtime import commands


def runtime_state(tmp_path, managers=None):
    return {
        "run_id": "runtime-1",
        "run_dir": str(tmp_path / "runtime-1"),
        "profile": "local",
        "setup_complete": True,
        "environment": {
            "QFW_ALLOCATION_MODE": "heterogeneous",
            "QFW_GROUP_0_NODELIST": "client-a",
            "QFW_GROUP_1_NODELIST": "service-a,service-b",
            "QFW_DIRECTORY_SERVICE_INFO": str(
                tmp_path / "directory-service.json"),
        },
        "local_dirsvc": {
            "name": "runtime-dirsvc",
            "endpoint": "service-a:18090",
        },
        "service_managers": managers or [],
        "processes": [],
    }


def test_runtime_status_composes_recorded_manager_health(
        tmp_path, monkeypatch):
    managers = [
        {
            "owner": "application",
            "role": "directory",
            "run_dir": str(tmp_path / "directory"),
        },
        {
            "owner": "application",
            "role": "qpm",
            "service_id": "fake-iqm",
            "run_dir": str(tmp_path / "fake-iqm"),
        },
    ]

    monkeypatch.setattr(
        commands.qfw_service_plane,
        "status",
        lambda run_dir: {
            "state": "ready",
            "run_dir": str(run_dir),
            "components": {},
        },
    )

    report = commands._runtime_status(runtime_state(tmp_path, managers))

    assert report["schema"] == "qfw-runtime-status-v1"
    assert report["state"] == "ready"
    assert report["allocation"] == {
        "mode": "heterogeneous",
        "group0": "client-a",
        "group1": "service-a,service-b",
    }
    assert report["directory_service"] == {
        "owner": "application",
        "name": "runtime-dirsvc",
        "endpoint": "service-a:18090",
        "connection_file": str(tmp_path / "directory-service.json"),
    }
    assert [item["state"] for item in report["service_managers"]] == [
        "ready",
        "ready",
    ]
    assert report["teardown_required"] is True


def test_runtime_status_reports_unavailable_manager_as_degraded(
        tmp_path, monkeypatch):
    managers = [{
        "owner": "application",
        "role": "qpm",
        "service_id": "fake-iqm",
        "run_dir": str(tmp_path / "missing"),
    }]

    def unavailable(_run_dir):
        raise FileNotFoundError("service-plane state is missing")

    monkeypatch.setattr(commands.qfw_service_plane, "status", unavailable)

    report = commands._runtime_status(runtime_state(tmp_path, managers))

    assert report["state"] == "degraded"
    assert report["service_managers"][0]["state"] == "unavailable"
    assert "state is missing" in report["service_managers"][0]["error"]


def test_qfw_status_json_uses_current_runtime(tmp_path, monkeypatch, capsys):
    state = runtime_state(tmp_path)
    captured = {}

    def read_state(run_dir):
        captured["run_dir"] = run_dir
        return state

    monkeypatch.setattr(commands.qfw_config, "read_state", read_state)

    assert commands.qfw_status(["--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert captured["run_dir"] is None
    assert report["state"] == "ready"
    assert report["runtime_state"] == state


def test_qfw_status_human_output_returns_nonzero_for_failed_setup(
        tmp_path, monkeypatch, capsys):
    state = runtime_state(tmp_path)
    state["setup_complete"] = False
    state["setup_error"] = "QPM failed to start"
    monkeypatch.setattr(
        commands.qfw_config, "read_state", lambda _run_dir: state)

    assert commands.qfw_status(["--run-dir", state["run_dir"]]) == 1

    output = capsys.readouterr().out
    assert "QFw runtime: failed" in output
    assert "run-id: runtime-1" in output
    assert "teardown-required: yes" in output
