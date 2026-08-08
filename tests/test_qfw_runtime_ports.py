from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))

from qfw_runtime import commands


def allocation():
    return {
        "mode": "local",
        "group0": ["client-a"],
        "group1": ["svc-a", "svc-b"],
        "group0_nodelist": "client-a",
        "group1_nodelist": "svc-a,svc-b",
        "groups": "GROUP_0=client-a:GROUP_1=svc-a,svc-b",
    }


def test_local_service_launch_specs_allocate_distinct_default_ports():
    services = [
        {"name": "nwqsim", "module": "svc_nwqsim_qpm"},
        {"name": "tnqvm", "module": "svc_tnqvm_qpm"},
    ]

    specs = commands._local_service_launch_specs(
        {}, services, ["nwqsim", "tnqvm"], allocation())

    assert [spec["listen_port"] for spec in specs] == [8290, 8390]
    assert [spec["telnet_port"] for spec in specs] == [8291, 8391]
    assert [spec["endpoint"] for spec in specs] == [
        "svc-a:8290",
        "svc-a:8390",
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
        {}, services, ["nwqsim", "tnqvm"], allocation())

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
            {}, services, ["nwqsim", "tnqvm"], allocation())


def test_local_service_launch_specs_resolve_manifest_placement():
    services = [
        {
            "name": "nwqsim",
            "module": "svc_nwqsim_qpm",
            "target": "group1-head",
            "assigned-hosts": "group1",
            "assigned-hosts-env": "QFW_QPM_ASSIGNED_HOSTS",
        },
        {
            "name": "client-side",
            "module": "svc_client",
            "target": "group0-head",
            "assigned-hosts": "all",
        },
    ]

    specs = commands._local_service_launch_specs(
        {}, services, ["nwqsim", "client-side"], allocation())

    assert specs[0]["target"] == "svc-a"
    assert specs[0]["assigned_hosts"] == "svc-a,svc-b"
    assert specs[0]["assigned_hosts_env"] == "QFW_QPM_ASSIGNED_HOSTS"
    assert specs[0]["endpoint"] == "svc-a:8290"
    assert specs[1]["target"] == "client-a"
    assert specs[1]["assigned_hosts"] == "client-a,svc-a,svc-b"
    assert specs[1]["endpoint"] == "client-a:8390"


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
        "local_services": {"start-prte": False, "start-qpm": True},
        "environment": {
            "QFW_ALLOCATION_MODE": "local",
            "QFW_GROUP_0_NODELIST": "client-a",
            "QFW_GROUP_1_NODELIST": "svc-a,svc-b",
            "QFW_GROUPS": "GROUP_0=client-a:GROUP_1=svc-a,svc-b",
        },
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
        "svc-a:8290",
        "svc-a:8390",
    ]


def test_start_job_local_services_starts_prte_before_services(
        tmp_path, monkeypatch):
    manifest = tmp_path / "services.yaml"
    manifest.write_text(
        "\n".join([
            "services:",
            "  - name: nwqsim",
            "    module: svc_nwqsim_qpm",
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
        calls.append((list(argv), dict(env)))
        if argv[0] == "prte":
            Path(env["QFW_DVM_URI_PATH"]).parent.mkdir(
                parents=True, exist_ok=True)
            Path(env["QFW_DVM_URI_PATH"]).write_text(
                "dvm-uri\n", encoding="utf-8")
            return
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
        "local_services": {"start-prte": True, "start-qpm": True},
        "environment": {
            "QFW_DVM_URI_PATH": str(run_dir / "prte_dvm" / "dvm-uri"),
            "QFW_ALLOCATION_MODE": "local",
            "QFW_GROUP_0_NODELIST": "client-a",
            "QFW_GROUP_1_NODELIST": "svc-a,svc-b",
            "QFW_GROUPS": "GROUP_0=client-a:GROUP_1=svc-a,svc-b",
            "QFW_JOB_ID": "12345",
        },
        "processes": [],
        "directory_requirements": [
            {"scope": "allocation-local", "connect_timeout_seconds": 1},
        ],
        "service_manifest": str(manifest),
    }

    commands._start_job_local_services(state)

    assert calls[0][0][:5] == [
        "prte",
        "--host",
        "svc-a:*,svc-b:*",
        "--report-uri",
        str(run_dir / "prte_dvm" / "dvm-uri"),
    ]
    assert "SLURM_JOB_ID=12345" in calls[0][0]
    assert "SLURM_JOBID=12345" in calls[0][0]
    assert calls[1][0][0] == "/usr/bin/qfw-service-start"
    assert calls[1][1]["QFW_DVM_URI_PATH"] == str(
        run_dir / "prte_dvm" / "dvm-uri")
    assert calls[1][1]["SLURM_JOB_ID"] == "12345"
    assert calls[1][1]["SLURM_JOBID"] == "12345"
    assert [process["role"] for process in state["processes"]] == [
        "prte-dvm",
        "service",
    ]


def test_cleanup_prte_uses_dvm_uri_without_default_pkill(tmp_path,
                                                         monkeypatch):
    uri_path = tmp_path / "prte_dvm" / "dvm-uri"
    uri_path.parent.mkdir()
    uri_path.write_text("dvm-uri\n", encoding="utf-8")
    calls = []

    def fake_run(argv, stdout=None, stderr=None, check=None):
        calls.append(list(argv))
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    commands._cleanup_prte({"uri_path": str(uri_path)})

    assert calls == [["pterm", "--dvm", f"file:{uri_path}"]]
    assert not uri_path.parent.exists()


def test_cleanup_prte_can_force_legacy_pkill(monkeypatch):
    calls = []

    def fake_run(argv, stdout=None, stderr=None, check=None):
        calls.append(list(argv))
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    commands._cleanup_prte({"force_cleanup": True})

    assert calls == [
        ["pkill", "-9", "prte"],
        ["pkill", "-9", "prted"],
    ]
