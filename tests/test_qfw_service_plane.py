import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))

from qfw_runtime import service_plane


def write_site_configuration(tmp_path, services):
    manifest = tmp_path / "services.yaml"
    manifest.write_text(
        "services:\n" + "".join(
            f"  - name: {name}\n"
            f"    module: {module}\n"
            f"    credential-mode: no-secret\n"
            f"    provider-launch:\n"
            f"      type: {provider}\n"
            for name, module, provider in services
        ),
        encoding="utf-8",
    )
    site = tmp_path / "site.yaml"
    site.write_text(
        "\n".join([
            "install:",
            f"  qfw-prefix: {tmp_path / 'qfw'}",
            f"  defw-prefix: {tmp_path / 'defw'}",
            "directory-service:",
            "  name: test-dirsvc",
            "  listen-port: 18090",
            f"  connection-file: {tmp_path / 'directory-service.json'}",
            "service:",
            f"  manifest: {manifest}",
            f"  device-access-config: {tmp_path / 'device-access.yaml'}",
            "",
        ]),
        encoding="utf-8",
    )
    return site, manifest


def write_runtime_configuration(tmp_path, *, prte, services=None):
    runtime = tmp_path / "runtime.yaml"
    lines = [
        "resolver:",
        "  scope-order:",
        "    - local",
        "local-services:",
        f"  start-prte: {'true' if prte else 'false'}",
        "  start-dirsvc: true",
        "  start-qpm: true",
    ]
    if services:
        lines.extend(["  services:", *[f"    - {name}" for name in services]])
    runtime.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return runtime


def parse_role_start_args(role, *argv):
    program = "qfw-dir-svc" if role == "directory" else "qfw-qpm-svc"
    return service_plane._role_argument_parser(program, role).parse_args(
        ["start", *argv])


def test_empty_run_dir_is_rejected(capsys):
    assert service_plane.directory_service_main([
        "start", "--run-dir", "", "--dry-run",
    ]) == 1
    assert "--run-dir must not be empty" in capsys.readouterr().err


def test_site_dry_run_generates_state_and_supports_status_and_stop(
        tmp_path, capsys):
    site, _manifest = write_site_configuration(tmp_path, [
        ("iqm-test", "svc_iqm_qpm", "remote-api"),
    ])
    runtime = tmp_path / "site-runtime.yaml"
    runtime.write_text(
        "resolver:\n  scope-order:\n    - site\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"

    assert service_plane.directory_service_main([
        "start",
        "--run-dir", str(run_dir),
        "--site-config", str(site),
        "--runtime-config", str(runtime),
        "--dry-run",
    ]) == 0
    capsys.readouterr()

    state_path = run_dir / "state" / "service-plane.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["state"] == "ready"
    assert state["configuration"]["components"] == {
        "directory": True,
        "prte": False,
        "qpm": False,
    }
    assert set(state["components"]) == {"directory"}
    assert state["configuration"]["service_ids"] == []

    assert service_plane.directory_service_main([
        "status", "--run-dir", str(run_dir),
    ]) == 0
    capsys.readouterr()
    assert service_plane.directory_service_main([
        "stop", "--run-dir", str(run_dir),
    ]) == 0
    capsys.readouterr()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["state"] == "stopped"
    assert all(
        component["state"] == "stopped"
        for component in state["components"].values()
    )


def test_application_configuration_controls_optional_prte(tmp_path):
    site, manifest = write_site_configuration(tmp_path, [
        ("fake-iqm", "svc_fake_iqm_qpm", "internal"),
    ])
    runtime = write_runtime_configuration(
        tmp_path, prte=False, services=["fake-iqm"])
    with runtime.open("a", encoding="utf-8") as stream:
        stream.write(f"  service-manifest: {manifest}\n")
    run_dir = tmp_path / "fake-run"
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "directory-1",
        "name": "test-dirsvc",
        "endpoint": "localhost:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")

    args = parse_role_start_args(
        "qpm",
        "--run-dir", str(run_dir),
        "--site-config", str(site),
        "--runtime-config", str(runtime),
        "--scope", "application",
        "--service-id", "fake-iqm",
        "--directory-service-info", str(connection_file),
        "--dry-run",
    )
    state = service_plane.start(args)

    assert state["configuration"]["components"]["prte"] is False
    assert "prte-dvm" not in state["components"]
    assert state["services"][0]["provider_launch"]["type"] == "internal"


def test_application_service_manifest_enables_required_prte(tmp_path):
    site, manifest = write_site_configuration(tmp_path, [
        ("mpi-smoke", "svc_mpi_smoke", "mpi"),
    ])
    runtime = tmp_path / "runtime.yaml"
    runtime.write_text(
        "\n".join([
            "resolver:",
            "  scope-order:",
            "    - local",
            "local-services:",
            "  start-dirsvc: true",
            "  start-qpm: true",
            "  services:",
            "    - mpi-smoke",
            f"  service-manifest: {manifest}",
            "",
        ]),
        encoding="utf-8",
    )
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "directory-1",
        "name": "test-dirsvc",
        "endpoint": "localhost:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")

    state = service_plane.start(parse_role_start_args(
        "qpm",
        "--run-dir", str(tmp_path / "mpi-run"),
        "--site-config", str(site),
        "--runtime-config", str(runtime),
        "--scope", "application",
        "--service-id", "mpi-smoke",
        "--directory-service-info", str(connection_file),
        "--dry-run",
    ))

    assert state["configuration"]["components"]["prte"] is True
    assert state["components"]["prte-dvm"]["state"] == "ready"


def test_application_service_without_provider_does_not_enable_prte(tmp_path):
    site, manifest = write_site_configuration(tmp_path, [
        ("shim", "svc_lib_qpm", ""),
    ])
    runtime = tmp_path / "runtime.yaml"
    runtime.write_text(
        "\n".join([
            "resolver:",
            "  scope-order:",
            "    - local",
            "local-services:",
            "  start-qpm: true",
            "  services:",
            "    - shim",
            f"  service-manifest: {manifest}",
            "",
        ]),
        encoding="utf-8",
    )
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "directory-1",
        "name": "test-dirsvc",
        "endpoint": "localhost:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")

    state = service_plane.start(parse_role_start_args(
        "qpm",
        "--run-dir", str(tmp_path / "shim-run"),
        "--site-config", str(site),
        "--runtime-config", str(runtime),
        "--scope", "application",
        "--service-id", "shim",
        "--directory-service-info", str(connection_file),
        "--dry-run",
    ))

    assert state["configuration"]["components"]["prte"] is False
    assert "prte-dvm" not in state["components"]


def test_directory_lifecycle_publishes_connection_record(
        tmp_path, monkeypatch):
    site, _manifest = write_site_configuration(tmp_path, [
        ("iqm-test", "svc_iqm_qpm", "remote-api"),
    ])
    runtime = tmp_path / "site-runtime.yaml"
    runtime.write_text("resolver:\n  scope-order:\n    - site\n")
    calls = []

    def fake_run_on_node(node, command, env, allocation, check=True):
        calls.append((node, list(command)))
        pid_file = Path(command[command.index("--pid-file") + 1])
        ready_file = Path(command[command.index("--ready-file") + 1])
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("3101\n", encoding="utf-8")
        ready_file.write_text('{"ready": true}\n', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(service_plane, "_run_on_node", fake_run_on_node)
    monkeypatch.setattr(service_plane, "_command_path", lambda name: name)
    monkeypatch.setattr(service_plane, "_terminate_pid", lambda *args: None)
    monkeypatch.setattr(service_plane, "_allocation_context", lambda: {
        "mode": "slurm",
        "group0": ["directory-a"],
        "group1": ["qpm-a"],
        "group0_nodelist": "directory-a",
        "group1_nodelist": "qpm-a",
    })
    parser = service_plane._role_argument_parser("qfw-dir-svc", "directory")
    args = parser.parse_args([
        "start", "--run-dir", str(tmp_path / "dir-run"),
        "--site-config", str(site), "--runtime-config", str(runtime),
        "--node", "directory-a",
    ])

    state = service_plane.start(args)
    connection_file = tmp_path / "directory-service.json"
    connection = json.loads(connection_file.read_text(encoding="utf-8"))
    assert set(state["components"]) == {"directory"}
    assert connection["endpoint"] == "directory-a:18090"
    assert connection["ready"] is True
    service_plane.stop(tmp_path / "dir-run")
    connection = json.loads(connection_file.read_text(encoding="utf-8"))
    assert connection["ready"] is False


def test_qpm_lifecycle_consumes_connection_record(tmp_path, monkeypatch):
    site, _manifest = write_site_configuration(tmp_path, [
        ("iqm-test", "svc_iqm_qpm", "remote-api"),
    ])
    runtime = tmp_path / "site-runtime.yaml"
    runtime.write_text("resolver:\n  scope-order:\n    - site\n")
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "directory-1",
        "name": "test-dirsvc",
        "endpoint": "directory-a:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(service_plane, "_allocation_context", lambda: {
        "mode": "slurm",
        "group0": ["directory-a"],
        "group1": ["qpm-a"],
        "group0_nodelist": "directory-a",
        "group1_nodelist": "qpm-a",
    })
    parser = service_plane._role_argument_parser("qfw-qpm-svc", "qpm")
    args = parser.parse_args([
        "start", "--run-dir", str(tmp_path / "qpm-run"),
        "--site-config", str(site), "--runtime-config", str(runtime),
        "--service-id", "iqm-test", "--node", "qpm-a", "--dry-run",
    ])

    state = service_plane.start(args)

    assert set(state["components"]) == {"qpm:iqm-test"}
    assert state["directory"]["endpoint"] == "directory-a:18090"
    assert state["services"][0]["target"] == "qpm-a"


def test_site_qpm_defaults_to_manager_host_without_allocation(
        tmp_path, monkeypatch):
    site, _manifest = write_site_configuration(tmp_path, [
        ("iqm-test", "svc_iqm_qpm", "remote-api"),
    ])
    runtime = tmp_path / "site-runtime.yaml"
    runtime.write_text("resolver:\n  scope-order:\n    - site\n")
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "directory-1",
        "name": "test-dirsvc",
        "endpoint": "directory-a:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(socket, "gethostname", lambda: "iqm-head")
    monkeypatch.setattr(service_plane, "_allocation_context", lambda: {
        "mode": "local",
        "group0": ["iqm-head"],
        "group1": ["iqm-head"],
        "group0_nodelist": "iqm-head",
        "group1_nodelist": "iqm-head",
    })
    parser = service_plane._role_argument_parser("qfw-qpm-svc", "qpm")
    args = parser.parse_args([
        "start", "--run-dir", str(tmp_path / "qpm-run"),
        "--site-config", str(site), "--runtime-config", str(runtime),
        "--service-id", "iqm-test", "--dry-run",
    ])

    state = service_plane.start(args)

    assert state["directory"]["endpoint"] == "directory-a:18090"
    assert state["services"][0]["target"] == "iqm-head"


def test_site_simulator_nodes_expand_from_environment(monkeypatch):
    allocation = {
        "mode": "local",
        "group0": ["qpm-a"],
        "group1": ["qpm-a"],
    }
    monkeypatch.setenv("QFW_SIMULATOR_NODES", "sim-a,sim-b")

    assert service_plane._resolve_host_policy(
        "${QFW_SIMULATOR_NODES}", allocation) == "sim-a,sim-b"
    assert service_plane._dvm_nodes(
        {"prte": {"hosts": "${QFW_SIMULATOR_NODES}"}},
        [], allocation, {}, "site") == ["sim-a", "sim-b"]


def test_independent_application_qpms_use_manifest_port_offsets(tmp_path):
    site, manifest = write_site_configuration(tmp_path, [
        ("nwqsim", "svc_nwqsim_qpm", "mpi"),
        ("tnqvm", "svc_tnqvm_qpm", "mpi"),
    ])
    runtime = write_runtime_configuration(tmp_path, prte=False)
    with runtime.open("a", encoding="utf-8") as stream:
        stream.write(f"  service-manifest: {manifest}\n")
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "directory-1",
        "name": "test-dirsvc",
        "endpoint": "service-a:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")
    allocation = {
        "mode": "slurm",
        "group0": ["app-a"],
        "group1": ["service-a"],
        "group0_nodelist": "app-a",
        "group1_nodelist": "service-a",
    }

    nwqsim = service_plane.start_role(
        "qpm",
        run_dir=tmp_path / "nwqsim-run",
        site_config=site,
        runtime_config=runtime,
        scope="application",
        service_id="nwqsim",
        directory_service_info=connection_file,
        dry_run=True,
        allocation=allocation,
    )
    tnqvm = service_plane.start_role(
        "qpm",
        run_dir=tmp_path / "tnqvm-run",
        site_config=site,
        runtime_config=runtime,
        scope="application",
        service_id="tnqvm",
        directory_service_info=connection_file,
        dry_run=True,
        allocation=allocation,
    )

    assert nwqsim["services"][0]["listen_port"] == 8290
    assert nwqsim["services"][0]["telnet_port"] == 8291
    assert tnqvm["services"][0]["listen_port"] == 8390
    assert tnqvm["services"][0]["telnet_port"] == 8391


def test_application_roles_place_control_plane_on_group1_head(
        tmp_path, monkeypatch):
    site, manifest = write_site_configuration(tmp_path, [
        ("nwqsim", "svc_nwqsim_qpm", "mpi"),
    ])
    runtime = write_runtime_configuration(
        tmp_path, prte=True, services=["nwqsim"])
    with runtime.open("a", encoding="utf-8") as stream:
        stream.write(f"  service-manifest: {manifest}\n")
    allocation = {
        "mode": "heterogeneous",
        "group0": ["app-a"],
        "group1": ["sim-a", "sim-b"],
        "group0_nodelist": "app-a",
        "group1_nodelist": "sim-a,sim-b",
    }
    monkeypatch.setattr(
        service_plane, "_allocation_context", lambda: allocation)
    dir_parser = service_plane._role_argument_parser(
        "qfw-dir-svc", "directory")
    directory_state = service_plane.start(dir_parser.parse_args([
        "start", "--run-dir", str(tmp_path / "directory-run"),
        "--site-config", str(site), "--runtime-config", str(runtime),
        "--scope", "application", "--dry-run",
    ]))
    assert directory_state["directory"]["target"] == "sim-a"

    connection_file = tmp_path / "application-directory.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "application-directory",
        "name": "test-dirsvc",
        "endpoint": "sim-a:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")
    qpm_parser = service_plane._role_argument_parser(
        "qfw-qpm-svc", "qpm")
    qpm_state = service_plane.start(qpm_parser.parse_args([
        "start", "--run-dir", str(tmp_path / "qpm-run"),
        "--site-config", str(site), "--runtime-config", str(runtime),
        "--scope", "application", "--service-id", "nwqsim",
        "--directory-service-info", str(connection_file), "--dry-run",
    ]))
    assert qpm_state["services"][0]["target"] == "sim-a"
    assert qpm_state["dvm"]["launch_node"] == "sim-a"
    assert qpm_state["dvm"]["nodes"] == ["sim-a", "sim-b"]


def test_service_plane_rejects_repeated_service_id():
    parser = service_plane._role_argument_parser("qfw-qpm-svc", "qpm")
    with pytest.raises(SystemExit):
        parser.parse_args([
            "start", "--run-dir", "/tmp/qfw-test",
            "--service-id", "one", "--service-id", "two",
        ])


def test_application_prte_configuration_creates_and_stops_dvm_state(tmp_path):
    site, manifest = write_site_configuration(tmp_path, [
        ("nwqsim", "svc_nwqsim_qpm", "mpi"),
    ])
    runtime = write_runtime_configuration(
        tmp_path, prte=True, services=["nwqsim"])
    with runtime.open("a", encoding="utf-8") as stream:
        stream.write(f"  service-manifest: {manifest}\n")
    run_dir = tmp_path / "nwqsim-run"

    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "directory-1",
        "name": "test-dirsvc",
        "endpoint": "localhost:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")
    args = parse_role_start_args(
        "qpm",
        "--run-dir", str(run_dir),
        "--site-config", str(site),
        "--runtime-config", str(runtime),
        "--scope", "application",
        "--service-id", "nwqsim",
        "--directory-service-info", str(connection_file),
        "--dry-run",
    )
    state = service_plane.start(args)
    uri_path = Path(state["dvm"]["uri_path"])

    assert state["components"]["prte-dvm"]["state"] == "ready"
    assert uri_path.is_file()
    service_plane.stop(run_dir)
    assert not uri_path.exists()


def test_prte_start_uses_keepalive_mode(tmp_path, monkeypatch):
    uri_path = tmp_path / "prte_dvm" / "dvm-uri"
    state = {
        "run_dir": str(tmp_path),
        "owner": "application",
        "dry_run": False,
        "dvm": {
            "launch_node": "sim-a",
            "nodes": ["sim-a"],
            "uri_path": str(uri_path),
        },
        "allocation": {
            "mode": "local",
            "group0": ["sim-a"],
            "group1": ["sim-a"],
        },
        "components": {},
    }
    calls = []

    def fake_run_on_node(node, command, env, allocation, check=True):
        calls.append((node, list(command)))
        uri_path.write_text("test-dvm-uri\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(service_plane, "_run_on_node", fake_run_on_node)
    monkeypatch.setattr(
        service_plane, "_command_path", lambda name, env=None: name)
    monkeypatch.setattr(service_plane.os, "geteuid", lambda: 1000)

    service_plane._start_prte(state, timeout=1)

    assert calls == [("sim-a", [
        "prte", "--host", "sim-a:*", "--report-uri", str(uri_path),
        "--keepalive", "0", "--daemonize",
    ])]


def test_qpm_start_composes_private_process_launcher(tmp_path, monkeypatch):
    site, _manifest = write_site_configuration(tmp_path, [
        ("iqm-test", "svc_iqm_qpm", "remote-api"),
    ])
    runtime = tmp_path / "site-runtime.yaml"
    runtime.write_text(
        "resolver:\n  scope-order:\n    - site\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "live-run"
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "directory-1",
        "name": "test-dirsvc",
        "endpoint": f"{socket.gethostname()}:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")
    calls = []
    terminated = []

    def fake_run_on_node(node, command, env, allocation, check=True):
        calls.append((node, list(command), dict(env)))
        if "--pid-file" in command:
            pid_file = Path(command[command.index("--pid-file") + 1])
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(f"{2000 + len(calls)}\n", encoding="utf-8")
        if "--ready-file" in command:
            ready_file = Path(command[command.index("--ready-file") + 1])
            ready_file.write_text('{"ready": true}\n', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(service_plane, "_run_on_node", fake_run_on_node)
    monkeypatch.setattr(
        service_plane,
        "_terminate_pid",
        lambda pid, node, allocation: terminated.append((pid, node)),
    )

    args = parse_role_start_args(
        "qpm",
        "--run-dir", str(run_dir),
        "--site-config", str(site),
        "--runtime-config", str(runtime),
        "--service-id", "iqm-test",
        "--directory-service-info", str(connection_file),
    )
    state = service_plane.start(args)

    assert state["state"] == "ready"
    assert [call[1][:4] for call in calls] == [[
        sys.executable, "-m", "qfw_runtime._process_launcher", "qpm",
    ]]
    qpm_env = calls[0][2]
    assert qpm_env["QFW_SERVICE_SCOPE"] == "site"
    assert qpm_env["QFW_SITE_DIRSVC_ENDPOINTS"] == (
        f"{socket.gethostname()}:18090")

    stopped = service_plane.stop(run_dir)
    assert stopped["state"] == "stopped"
    assert [pid for pid, _node in terminated] == [2001]


def test_foreground_run_stops_on_sigterm(tmp_path):
    site, _manifest = write_site_configuration(tmp_path, [
        ("iqm-test", "svc_iqm_qpm", "remote-api"),
    ])
    runtime = tmp_path / "site-runtime.yaml"
    runtime.write_text(
        "resolver:\n  scope-order:\n    - site\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    state_path = run_dir / "state" / "service-plane.json"
    environment = os.environ.copy()
    source_setup = str(Path(__file__).resolve().parents[1] / "setup")
    environment["PYTHONPATH"] = source_setup + os.pathsep + environment.get(
        "PYTHONPATH", "")
    environment["QFW_SERVICE_LIFECYCLE_ROLE"] = "directory"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m", "qfw_runtime.service_plane",
            "run",
            "--run-dir", str(run_dir),
            "--site-config", str(site),
            "--runtime-config", str(runtime),
            "--dry-run",
            "--poll-interval", "0.05",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("manager_pid") == process.pid:
                    break
            time.sleep(0.02)
        else:
            raise AssertionError("foreground manager did not become ready")

        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, (stdout, stderr)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["state"] == "stopped"
