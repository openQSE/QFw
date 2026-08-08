from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))

from qfw_runtime import commands


def test_local_service_launch_specs_allocate_distinct_default_ports():
    services = [
        {"name": "nwqsim", "module": "svc_nwqsim_qpm"},
        {"name": "tnqvm", "module": "svc_tnqvm_qpm"},
    ]

    specs = commands._local_service_launch_specs(
        {}, services, ["nwqsim", "tnqvm"])

    assert [spec["listen_port"] for spec in specs] == [8290, 8390]
    assert [spec["telnet_port"] for spec in specs] == [8291, 8391]
    assert [spec["endpoint"] for spec in specs] == [
        "127.0.0.1:8290",
        "127.0.0.1:8390",
    ]


def test_local_service_launch_specs_honor_explicit_manifest_ports():
    services = [
        {
            "name": "nwqsim",
            "module": "svc_nwqsim_qpm",
            "listen-port": 9020,
            "telnet-port": 9021,
        },
        {"name": "tnqvm", "module": "svc_tnqvm_qpm"},
    ]

    specs = commands._local_service_launch_specs(
        {}, services, ["nwqsim", "tnqvm"])

    assert [spec["listen_port"] for spec in specs] == [9020, 8290]
    assert [spec["telnet_port"] for spec in specs] == [9021, 8291]


def test_local_service_launch_specs_reject_duplicate_ports():
    services = [
        {
            "name": "nwqsim",
            "module": "svc_nwqsim_qpm",
            "listen-port": 9020,
        },
        {
            "name": "tnqvm",
            "module": "svc_tnqvm_qpm",
            "telnet-port": 9020,
        },
    ]

    with pytest.raises(ValueError, match="duplicate local service"):
        commands._local_service_launch_specs(
            {}, services, ["nwqsim", "tnqvm"])


def test_start_job_local_services_passes_distinct_ports(tmp_path, monkeypatch):
    manifest = tmp_path / "services.yaml"
    manifest.write_text(
        "\n".join([
            "services:",
            "  - name: nwqsim",
            "    module: svc_nwqsim_qpm",
            "  - name: tnqvm",
            "    module: svc_tnqvm_qpm",
            "",
        ]),
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    run_dir = tmp_path / "run"
    state_dir.mkdir()
    run_dir.mkdir()
    calls = []

    def fake_command_path(name, env=None):
        return Path(f"/usr/bin/{name}")

    def fake_run_checked(argv, env):
        calls.append(list(argv))
        pid_path = Path(argv[argv.index("--pid-file") + 1])
        pid_path.write_text(f"{1000 + len(calls)}\n", encoding="utf-8")
        ready_path = Path(argv[argv.index("--ready-file") + 1])
        ready_path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(commands, "_command_path", fake_command_path)
    monkeypatch.setattr(commands, "_run_checked", fake_run_checked)

    state = {
        "run_id": "test",
        "run_base_dir": str(tmp_path),
        "run_dir": str(run_dir),
        "state_dir": str(state_dir),
        "site_config": str(tmp_path / "site.yaml"),
        "local_services": {"start-qpm": True},
        "environment": {},
        "processes": [],
        "directory_requirements": [
            {"scope": "allocation-local", "connect_timeout_seconds": 1},
        ],
        "service_manifest": str(manifest),
    }

    commands._start_job_local_services(state)

    assert len(calls) == 2
    assert [call[call.index("--listen-port") + 1] for call in calls] == [
        "8290",
        "8390",
    ]
    assert [call[call.index("--telnet-port") + 1] for call in calls] == [
        "8291",
        "8391",
    ]
    assert [process["endpoint"] for process in state["processes"]] == [
        "127.0.0.1:8290",
        "127.0.0.1:8390",
    ]
