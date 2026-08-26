from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))

from qfw_runtime import commands
from qfw_runtime import _process_launcher as process_launcher


def allocation():
    return {
        "mode": "local",
        "group0": ["client-a"],
        "group1": ["svc-a", "svc-b"],
        "group0_nodelist": "client-a",
        "group1_nodelist": "svc-a,svc-b",
        "groups": "GROUP_0=client-a:GROUP_1=svc-a,svc-b",
    }


def hetero_allocation():
    return {
        "mode": "heterogeneous",
        "group0": ["client-a"],
        "group1": ["svc-a", "svc-b"],
        "group0_nodelist": "client-a",
        "group1_nodelist": "svc-a,svc-b",
        "groups": "GROUP_0=client-a:GROUP_1=svc-a,svc-b",
    }


def test_direct_qpm_readiness_uses_qpm_control_binding():
    binding = process_launcher._endpoint_binding_record(
        "qpm.example:9020", "svc_iqm_qpm")

    assert binding["selected_binding"] == {
        "binding_name": "control",
        "client_module": "api_qpm_control",
        "client_class": "QPMControl",
        "service_module": "svc_iqm_qpm.svc_qpm",
        "service_class": "QPM",
        "version": 1,
    }


def test_direct_qpm_readiness_preserves_qualified_service_module():
    binding = process_launcher._endpoint_binding_record(
        "qpm.example:9020", "svc_iqm_qpm.svc_qpm")

    assert (
        binding["selected_binding"]["service_module"]
        == "svc_iqm_qpm.svc_qpm"
    )


def test_direct_qpm_readiness_calls_is_ready(monkeypatch):
    class QPMControl:
        def is_ready(self):
            return {"ready": True}

    class DEFw:
        def connect_to_binding(self, binding):
            self.binding = binding
            return QPMControl()

    fake_defw = DEFw()
    monkeypatch.setitem(sys.modules, "defw", fake_defw)

    assert process_launcher._defw_endpoint_ready_unbounded(
        "qpm.example:9020", "svc_iqm_qpm")
    assert fake_defw.binding["selected_binding"]["client_class"] == "QPMControl"


def test_direct_qpm_readiness_rejects_ready_alias(monkeypatch):
    class ObsoleteQPMClient:
        ready = True

    class DEFw:
        def connect_to_binding(self, binding):
            return ObsoleteQPMClient()

    monkeypatch.setitem(sys.modules, "defw", DEFw())

    assert not process_launcher._defw_endpoint_ready_unbounded(
        "qpm.example:9020", "svc_iqm_qpm")


def test_private_qpm_launcher_loads_service_paths_from_site(
        tmp_path, monkeypatch):
    device_path = tmp_path / "device-access.yaml"
    manifest_path = tmp_path / "site-services.yaml"
    manifest_path.write_text(
        "services:\n  - name: iqm\n    module: svc_iqm_qpm\n",
        encoding="utf-8",
    )
    site_path = tmp_path / "site.yaml"
    site_path.write_text(
        "\n".join([
            "directory-service:",
            "  endpoint: 127.0.0.1:8090",
            "service:",
            f"  manifest: {manifest_path}",
            f"  device-access-config: {device_path}",
            "",
        ]),
        encoding="utf-8",
    )
    captured = {}

    def fake_start(name, env, *args):
        captured["name"] = name
        captured["env"] = dict(env)
        return 0

    monkeypatch.setenv("QFW_SERVICE_SCOPE", "site")
    monkeypatch.setattr(
        process_launcher, "_start_defw_owned_process", fake_start)

    rc = process_launcher.start_qpm([
        "--service-id", "iqm",
        "--site-config", str(site_path),
        "--run-dir", str(tmp_path / "run"),
        "--operation-mode", "direct",
    ])

    assert rc == 0
    assert captured["name"] == "iqm"
    assert captured["env"]["QFW_SERVICE_CONFIG"] == str(manifest_path)
    assert captured["env"]["QFW_DEVICE_ACCESS_CFG"] == str(device_path)
    assert captured["env"]["QFW_SITE_CONFIG"] == str(site_path)


def test_private_qpm_launcher_rejects_removed_config_overrides(
        tmp_path, monkeypatch):
    site_path = tmp_path / "site.yaml"
    site_path.write_text(
        "\n".join([
            "service: {}",
        ]),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit):
        process_launcher.start_qpm([
            "--service-id", "iqm",
            "--module", "svc_iqm_qpm",
            "--site-config", str(site_path),
            "--device-access-config", str(tmp_path / "device.yaml"),
        ])


def test_start_job_local_services_delegates_to_split_managers(
        tmp_path, monkeypatch):
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

    def fake_start_role(role, **kwargs):
        calls.append((role, dict(kwargs)))
        if role == "directory":
            return {
                "directory": {
                    "name": "qfw-local-dirsvc",
                    "endpoint": "svc-a:18090",
                    "telnet_port": 18091,
                    "connection_file": str(
                        run_dir / "service-plane" / "directory" /
                        "directory-service.json"),
                },
            }
        index = 0 if kwargs["service_id"] == "nwqsim" else 1
        return {
            "services": [{
                "service_id": kwargs["service_id"],
                "target": "svc-a",
                "listen_port": 8290 + index * 100,
                "telnet_port": 8291 + index * 100,
            }],
        }

    monkeypatch.setattr(
        commands.qfw_service_plane, "start_role", fake_start_role)

    state = {
        "run_id": "test",
        "run_base_dir": str(tmp_path),
        "run_dir": str(run_dir),
        "state_dir": str(state_dir),
        "site_config": str(tmp_path / "site.yaml"),
        "runtime_config": str(tmp_path / "runtime.yaml"),
        "local_services": {
            "start-prte": True,
            "start-dirsvc": True,
            "start-qpm": True,
        },
        "environment": {
            "QFW_ALLOCATION_MODE": "local",
            "QFW_GROUP_0_NODELIST": "client-a",
            "QFW_GROUP_1_NODELIST": "svc-a,svc-b",
            "QFW_GROUPS": "GROUP_0=client-a:GROUP_1=svc-a,svc-b",
        },
        "service_managers": [],
        "directory_requirements": [
            {"scope": "allocation-local", "connect_timeout_seconds": 1},
        ],
        "service_manifest": str(manifest),
    }

    commands._start_job_local_services(state)

    assert [role for role, _kwargs in calls] == [
        "directory",
        "qpm",
        "qpm",
    ]
    assert [
        kwargs.get("service_id") for _role, kwargs in calls[1:]
    ] == ["nwqsim", "tnqvm"]
    directory_info = str(
        run_dir / "service-plane" / "directory" /
        "directory-service.json")
    assert calls[1][1]["directory_service_info"] == directory_info
    assert calls[2][1]["directory_service_info"] == directory_info
    assert [manager["role"] for manager in state["service_managers"]] == [
        "directory",
        "qpm",
        "qpm",
    ]
    assert state["environment"]["QFW_DIRECTORY_SERVICE_INFO"] == (
        directory_info)
    assert state["environment"]["QFW_LOCAL_DIRSVC_ENDPOINT"] == (
        "svc-a:18090")
    assert state["environment"]["QFW_QPM_SERVICE_IDS"] == "nwqsim,tnqvm"
    assert [launch["listen_port"] for launch in
            state["local_service_launches"]] == [8290, 8390]


def test_start_job_local_services_places_every_role_on_group1(
        tmp_path, monkeypatch):
    manifest = tmp_path / "services.yaml"
    manifest.write_text(
        "services:\n"
        "  - name: nwqsim\n"
        "    module: svc_nwqsim_qpm\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    run_dir = tmp_path / "run"
    state_dir.mkdir()
    run_dir.mkdir()
    allocation = hetero_allocation()
    calls = []

    def fake_start_role(role, **kwargs):
        calls.append((role, dict(kwargs)))
        if role == "directory":
            return {
                "directory": {
                    "name": "qfw-local-dirsvc",
                    "endpoint": "svc-a:18090",
                    "telnet_port": 18091,
                    "connection_file": str(
                        run_dir / "service-plane" / "directory" /
                        "directory-service.json"),
                },
            }
        return {
            "services": [{
                "service_id": "nwqsim",
                "target": "svc-a",
                "assigned_hosts": "svc-a,svc-b",
                "listen_port": 8290,
                "telnet_port": 8291,
            }],
        }

    monkeypatch.setattr(
        commands.qfw_service_plane, "start_role", fake_start_role)
    state = {
        "run_id": "test",
        "run_base_dir": str(tmp_path),
        "run_dir": str(run_dir),
        "state_dir": str(state_dir),
        "site_config": str(tmp_path / "site.yaml"),
        "runtime_config": str(tmp_path / "runtime.yaml"),
        "local_services": {
            "start-prte": True,
            "start-dirsvc": True,
            "start-qpm": True,
        },
        "local_dirsvc": {},
        "environment": {
            "QFW_ALLOCATION_MODE": allocation["mode"],
            "QFW_GROUP_0_NODELIST": allocation["group0_nodelist"],
            "QFW_GROUP_1_NODELIST": allocation["group1_nodelist"],
            "QFW_GROUPS": allocation["groups"],
        },
        "service_managers": [],
        "directory_requirements": [{
            "scope": "allocation-local",
            "name": "qfw-local-dirsvc",
            "endpoint": "127.0.0.1:1",
            "connect_timeout_seconds": 1,
        }],
        "service_manifest": str(manifest),
    }

    commands._start_job_local_services(state)

    assert all(kwargs["scope"] == "application" for _role, kwargs in calls)
    assert all(kwargs["allocation"] == allocation for _role, kwargs in calls)
    assert state["local_dirsvc"]["host"] == "svc-a"
    assert state["directory_requirements"][0]["endpoint"] == (
        "svc-a:18090")


def test_cleanup_application_service_managers_uses_reverse_order(
        monkeypatch):
    stopped = []
    monkeypatch.setattr(
        commands.qfw_service_plane,
        "stop",
        lambda run_dir: stopped.append(str(run_dir)),
    )
    state = {
        "service_managers": [
            {
                "owner": "application",
                "role": "directory",
                "run_dir": "/run/directory",
            },
            {
                "owner": "application",
                "role": "qpm",
                "service_id": "nwqsim",
                "run_dir": "/run/qpm/nwqsim",
            },
            {
                "owner": "site",
                "role": "qpm",
                "service_id": "site-iqm",
                "run_dir": "/site/qpm/iqm",
            },
        ],
    }

    assert commands._cleanup_application_service_managers(state) == []
    assert stopped == [
        "/run/qpm/nwqsim",
        "/run/directory",
    ]


def test_private_process_launcher_uses_defw_python_wrapper(
        tmp_path, monkeypatch):
    pid_file = tmp_path / "svc.pid"
    ready_file = tmp_path / "svc-ready.json"
    log_dir = tmp_path / "logs"
    captured = {}

    class FakeProcess:
        pid = 1234

        def poll(self):
            return None

        def wait(self):
            return 0

    def fake_command_path(name, env=None):
        assert name == "defw-python"
        return Path("/usr/bin/defw-python")

    def fake_popen(argv, env, start_new_session, stdout, stderr):
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        captured["start_new_session"] = start_new_session
        captured["stdout_name"] = stdout.name
        captured["stderr_name"] = stderr.name
        return FakeProcess()

    monkeypatch.setattr(process_launcher, "_command_path", fake_command_path)
    monkeypatch.setattr(process_launcher.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        process_launcher,
        "_wait_process_ready",
        lambda process, timeout, name, ready_probe: None,
    )

    rc = process_launcher._start_defw_owned_process(
        "svc",
        {"DEFW_LOG_DIR": str(log_dir), "VIRTUAL_ENV": "/shared/venv"},
        pid_file,
        ready_file,
        5,
        True,
        False,
        {"role": "service"},
        lambda: True,
    )

    assert rc == 0
    assert captured["argv"] == ["/usr/bin/defw-python", "-d", "-x"]
    assert captured["env"]["VIRTUAL_ENV"] == "/shared/venv"
    assert captured["start_new_session"] is True
    assert Path(captured["stdout_name"]).name == "svc.stdout.log"
    assert Path(captured["stderr_name"]).name == "svc.stderr.log"
    assert pid_file.read_text(encoding="utf-8") == "1234\n"
    assert json.loads(ready_file.read_text(encoding="utf-8"))["role"] == (
        "service")


def test_qfw_srun_uses_group0_for_heterogeneous_allocation(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    state_dir = run_dir / "state"
    state_dir.mkdir(parents=True)
    state = {
        "setup_complete": True,
        "run_dir": str(run_dir),
        "environment": {
            "QFW_ALLOCATION_MODE": "heterogeneous",
            "QFW_GROUP_0_NODELIST": "client-a",
            "QFW_GROUP_1_NODELIST": "svc-a,svc-b",
            "QFW_GROUPS": "GROUP_0=client-a:GROUP_1=svc-a,svc-b",
        },
    }
    (state_dir / "runtime-state.json").write_text(
        json.dumps(state), encoding="utf-8")
    captured = {}

    def fake_command_path(name, env=None):
        assert name == "defw-python"
        return Path("/usr/bin/defw-python")

    def fake_run(argv, env):
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands, "_command_path", fake_command_path)
    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    rc = commands.qfw_srun([
        "--run-dir", str(run_dir),
        "app.py",
        "--shots", "10",
    ])

    assert rc == 0
    assert captured["argv"] == [
        "srun",
        "--het-group=0",
        "/usr/bin/defw-python",
        "app.py",
        "--shots", "10",
    ]
    assert captured["env"]["QFW_ALLOCATION_MODE"] == "heterogeneous"


def test_qfw_srun_runs_directly_for_local_allocation(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    state_dir = run_dir / "state"
    state_dir.mkdir(parents=True)
    state = {
        "setup_complete": True,
        "run_dir": str(run_dir),
        "environment": {
            "QFW_ALLOCATION_MODE": "local",
            "QFW_GROUP_0_NODELIST": "client-a",
            "QFW_GROUP_1_NODELIST": "client-a",
            "QFW_GROUPS": "GROUP_0=client-a:GROUP_1=client-a",
        },
    }
    (state_dir / "runtime-state.json").write_text(
        json.dumps(state), encoding="utf-8")
    captured = {}

    def fake_command_path(name, env=None):
        assert name == "defw-python"
        return Path("/usr/bin/defw-python")

    def fake_run(argv, env):
        captured["argv"] = list(argv)
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands, "_command_path", fake_command_path)
    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    rc = commands.qfw_srun(["--run-dir", str(run_dir), "app.py"])

    assert rc == 0
    assert captured["argv"] == ["/usr/bin/defw-python", "app.py"]


def test_qfw_srun_configures_application_defw_environment(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    state_dir = run_dir / "state"
    state_dir.mkdir(parents=True)
    state = {
        "setup_complete": True,
        "run_dir": str(run_dir),
        "environment": {
            "QFW_ALLOCATION_MODE": "heterogeneous",
            "QFW_GROUP_0_NODELIST": "client-a",
            "QFW_GROUP_1_NODELIST": "svc-a",
            "QFW_GROUPS": "GROUP_0=client-a:GROUP_1=svc-a",
            "QFW_LOCAL_DIRSVC_ENDPOINT": "svc-a:8090",
            "QFW_LOCAL_DIRSVC_NAME": "qfw-local-dirsvc",
            "DEFW_PARENT_HOSTNAME": "svc-a",
            "DEFW_PARENT_PORT": "8090",
            "DEFW_PARENT_NAME": "qfw-local-dirsvc",
        },
    }
    (state_dir / "runtime-state.json").write_text(
        json.dumps(state), encoding="utf-8")
    captured = {}

    def fake_command_path(name, env=None):
        assert name == "defw-python"
        return Path("/usr/bin/defw-python")

    def fake_run(argv, env):
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands, "_command_path", fake_command_path)
    monkeypatch.setattr(commands, "_free_tcp_port", lambda host: 45678)
    monkeypatch.setattr(commands, "_resolve_host_address",
                        lambda host: "10.0.0.2")
    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    rc = commands.qfw_srun(["--run-dir", str(run_dir), "app.py"])

    assert rc == 0
    assert captured["argv"] == [
        "srun",
        "--het-group=0",
        "/usr/bin/defw-python",
        "app.py",
    ]
    assert captured["env"]["DEFW_DISABLE_DIRSVC"] == "no"
    assert captured["env"]["DEFW_AGENT_TYPE"] == "agent"
    assert captured["env"]["DEFW_SHELL_TYPE"] == "cmdline"
    assert captured["env"]["DEFW_PARENT_ADDR"] == "10.0.0.2"
    assert captured["env"]["DEFW_LISTEN_PORT"] == "45678"
    assert captured["env"]["DEFW_LOG_DIR"] == str(
        run_dir / "application" / "logs")


def test_qfw_srun_honors_slurm_application_placement(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    state_dir = run_dir / "state"
    state_dir.mkdir(parents=True)
    state = {
        "setup_complete": True,
        "run_dir": str(run_dir),
        "environment": {
            "QFW_ALLOCATION_MODE": "slurm",
            "QFW_GROUP_0_NODELIST": "client-a,client-b",
            "QFW_GROUP_1_NODELIST": "svc-a",
            "QFW_GROUPS": "GROUP_0=client-a,client-b:GROUP_1=svc-a",
        },
    }
    (state_dir / "runtime-state.json").write_text(
        json.dumps(state), encoding="utf-8")
    captured = {}

    def fake_command_path(name, env=None):
        assert name == "defw-python"
        return Path("/usr/bin/defw-python")

    def fake_run(argv, env):
        captured["argv"] = list(argv)
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands, "_command_path", fake_command_path)
    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    rc = commands.qfw_srun([
        "--run-dir", str(run_dir),
        "--nodes", "1",
        "--ntasks", "1",
        "--nodelist", "client-b",
        "app.py",
    ])

    assert rc == 0
    assert captured["argv"] == [
        "srun",
        "--nodes", "1",
        "--ntasks", "1",
        "--nodelist", "client-b",
        "/usr/bin/defw-python",
        "app.py",
    ]


def test_qfw_srun_configures_site_directory_defw_environment(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    state_dir = run_dir / "state"
    state_dir.mkdir(parents=True)
    state = {
        "setup_complete": True,
        "run_dir": str(run_dir),
        "environment": {
            "QFW_ALLOCATION_MODE": "slurm",
            "QFW_GROUP_0_NODELIST": "client-a,client-b",
            "QFW_GROUP_1_NODELIST": "svc-a",
            "QFW_GROUPS": "GROUP_0=client-a,client-b:GROUP_1=svc-a",
            "QFW_SITE_DIRSVC_ENDPOINTS": "svc-a:8090",
            "QFW_SITE_DIRSVC_NAME": "qfw-site-dirsvc",
        },
    }
    (state_dir / "runtime-state.json").write_text(
        json.dumps(state), encoding="utf-8")
    captured = {}

    def fake_command_path(name, env=None):
        assert name == "defw-python"
        return Path("/usr/bin/defw-python")

    def fake_run(argv, env):
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands, "_command_path", fake_command_path)
    monkeypatch.setattr(commands, "_free_tcp_port", lambda host: 45679)
    monkeypatch.setattr(commands, "_resolve_host_address",
                        lambda host: "10.0.0.3")
    monkeypatch.setattr(commands.subprocess, "run", fake_run)

    rc = commands.qfw_srun([
        "--run-dir", str(run_dir),
        "--nodes", "1",
        "--ntasks", "1",
        "--nodelist", "client-a",
        "app.py",
    ])

    assert rc == 0
    assert captured["argv"][:7] == [
        "srun",
        "--nodes", "1",
        "--ntasks", "1",
        "--nodelist", "client-a",
    ]
    assert captured["env"]["DEFW_DISABLE_DIRSVC"] == "no"
    assert captured["env"]["DEFW_PARENT_HOSTNAME"] == "svc-a"
    assert captured["env"]["DEFW_PARENT_PORT"] == "8090"
    assert captured["env"]["DEFW_PARENT_NAME"] == "qfw-site-dirsvc"
    assert captured["env"]["DEFW_PARENT_ADDR"] == "10.0.0.3"
    assert captured["env"]["DEFW_LISTEN_PORT"] == "45679"
