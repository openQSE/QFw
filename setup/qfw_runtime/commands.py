import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import config as qfw_config
from . import service_plane as qfw_service_plane


COMMANDS = {
    "qfw-setup",
    "qfw-status",
    "qfw-srun",
    "qfw-teardown",
    "qfw-dirsvc-start",
    "qfw-service-start",
}
DEFW_ENDPOINT_PROBE_TIMEOUT_SECONDS = 1.0
DEFW_ENDPOINT_READY_PROBE = r"""
import sys
from qfw_runtime.commands import _defw_endpoint_ready_unbounded

raise SystemExit(
    0 if _defw_endpoint_ready_unbounded(sys.argv[1]) else 1)
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS:
        print("Usage: qfw_runtime.commands <command> [args...]", file=sys.stderr)
        return 2
    command = argv.pop(0)
    try:
        if command == "qfw-setup":
            return qfw_setup(argv)
        if command == "qfw-status":
            return qfw_status(argv)
        if command == "qfw-srun":
            return qfw_srun(argv)
        if command == "qfw-teardown":
            return qfw_teardown(argv)
        if command == "qfw-dirsvc-start":
            return qfw_dirsvc_start(argv)
        if command == "qfw-service-start":
            return qfw_service_start(argv)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return 1
    return 2


def qfw_setup(argv):
    parser = argparse.ArgumentParser(prog="qfw-setup")
    parser.add_argument("--site-config")
    parser.add_argument("--runtime-config")
    parser.add_argument("--profile")
    parser.add_argument("--run-id")
    parser.add_argument("--run-dir")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-start-services", action="store_true")
    args = parser.parse_args(argv)

    site_path = qfw_config.resolve_site_config(args.site_config)
    site = qfw_config.load_yaml(site_path)
    prefixes = qfw_config.site_install_prefixes(site)
    runtime_path = qfw_config.resolve_runtime_config(
        explicit=args.runtime_config,
        profile=args.profile,
        qfw_prefix_override=prefixes["qfw_prefix"],
    )
    runtime = qfw_config.load_yaml(runtime_path)
    state = qfw_config.prepare_run_state(
        site_path,
        runtime_path,
        site,
        runtime,
        profile=args.profile or os.environ.get("QFW_RUNTIME_PROFILE"),
        run_id=args.run_id,
        run_dir=args.run_dir,
        dry_run=args.dry_run,
    )

    try:
        if state["local_services"] and not (
                args.dry_run or args.no_start_services):
            _start_job_local_services(state)
        if not args.dry_run:
            _wait_required_directories(state)
        state["setup_complete"] = True
        state["setup_completed_at_ns"] = time.time_ns()
        qfw_config.write_state(state)
    except Exception as exc:
        state["setup_error"] = str(exc)
        qfw_config.write_state(state)
        _cleanup_application_service_managers(state)
        _cleanup_job_processes(state)
        qfw_config.clear_current_run(state)
        raise

    print(state["run_dir"])
    return 0


def qfw_status(argv):
    parser = argparse.ArgumentParser(prog="qfw-status")
    parser.add_argument("--run-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    state = qfw_config.read_state(args.run_dir)
    report = _runtime_status(state)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_runtime_status(report)
    return 0 if report["state"] == "ready" else 1


def qfw_srun(argv):
    parser = argparse.ArgumentParser(prog="qfw-srun")
    parser.add_argument("--run-dir")
    parser.add_argument("--nodes")
    parser.add_argument("--ntasks")
    parser.add_argument("--nodelist")
    parser.add_argument("--het-group")
    parser.add_argument("--exclusive", action="store_true")
    parser.add_argument("application", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if not args.application:
        parser.error("application command is required")

    state = qfw_config.read_state(args.run_dir)
    if not state.get("setup_complete"):
        print(
            f"QFw runtime setup is incomplete: {state.get('run_dir')}",
            file=sys.stderr,
        )
        return 1
    env = os.environ.copy()
    env.update(state.get("environment") or {})
    allocation = _allocation_context_from_env(env)
    _publish_allocation_environment(env, allocation)
    _configure_application_defw_environment(env, state)
    defw_python = _command_path("defw-python", env=env)
    command = _application_launch_command(
        [str(defw_python), *args.application],
        allocation,
        {
            "nodes": args.nodes,
            "ntasks": args.ntasks,
            "nodelist": args.nodelist,
            "het_group": args.het_group,
            "exclusive": args.exclusive,
        },
    )
    return subprocess.run(command, env=env).returncode


def qfw_teardown(argv):
    parser = argparse.ArgumentParser(prog="qfw-teardown")
    parser.add_argument("--run-dir")
    parser.add_argument("--keep-run-dir", action="store_true")
    args = parser.parse_args(argv)

    state = qfw_config.read_state(args.run_dir)
    errors = _cleanup_application_service_managers(state)
    errors.extend(_cleanup_job_processes(state, report_errors=False))
    qfw_config.clear_current_run(state)
    if not args.keep_run_dir:
        shutil.rmtree(state["run_dir"], ignore_errors=True)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    return 0


def _runtime_status(state):
    setup_complete = bool(state.get("setup_complete"))
    setup_error = state.get("setup_error")
    if setup_error:
        runtime_state = "failed"
    elif setup_complete:
        runtime_state = "ready"
    else:
        runtime_state = "incomplete"

    managers = []
    for manager in state.get("service_managers") or []:
        record = {
            "owner": manager.get("owner", ""),
            "role": manager.get("role", ""),
            "run_dir": manager.get("run_dir", ""),
        }
        if manager.get("service_id"):
            record["service_id"] = manager["service_id"]
        if record["owner"] != "application":
            record["state"] = "external"
            managers.append(record)
            continue
        try:
            manager_state = qfw_service_plane.status(record["run_dir"])
            record["state"] = manager_state.get("state", "unknown")
            record["status"] = manager_state
        except (OSError, RuntimeError, ValueError) as exc:
            record["state"] = "unavailable"
            record["error"] = str(exc)
        if record["state"] != "ready" and runtime_state == "ready":
            runtime_state = "degraded"
        managers.append(record)

    environment = state.get("environment") or {}
    directory = state.get("local_dirsvc") or {}
    directory_owner = "application"
    if not directory.get("endpoint"):
        directory = state.get("site_directory") or {}
        directory_owner = "site" if directory else ""
    directory_status = {
        "owner": directory_owner,
        "name": directory.get("name", ""),
        "endpoint": directory.get("endpoint", ""),
    }
    connection_file = (
        directory.get("connection_file") or
        environment.get("QFW_DIRECTORY_SERVICE_INFO")
    )
    if connection_file:
        directory_status["connection_file"] = connection_file

    allocation = {
        "mode": environment.get("QFW_ALLOCATION_MODE", ""),
        "group0": environment.get("QFW_GROUP_0_NODELIST", ""),
        "group1": environment.get("QFW_GROUP_1_NODELIST", ""),
    }
    return {
        "schema": "qfw-runtime-status-v1",
        "state": runtime_state,
        "run_id": state.get("run_id", ""),
        "run_dir": state.get("run_dir", ""),
        "profile": state.get("profile"),
        "setup": {
            "complete": setup_complete,
            "error": setup_error,
        },
        "allocation": allocation,
        "directory_service": directory_status,
        "service_managers": managers,
        "teardown_required": True,
        "runtime_state": state,
    }


def _print_runtime_status(report):
    print(f"QFw runtime: {report['state']}")
    print(f"  run-id: {report['run_id'] or '-'}")
    print(f"  run-dir: {report['run_dir'] or '-'}")
    print(f"  profile: {report['profile'] or '-'}")
    allocation = report["allocation"]
    print(f"  allocation: {allocation['mode'] or '-'}")
    directory = report["directory_service"]
    if directory["endpoint"]:
        print(
            "  directory-service: "
            f"{directory['name'] or '-'} at {directory['endpoint']} "
            f"({directory['owner']})"
        )
    else:
        print("  directory-service: -")
    managers = report["service_managers"]
    if managers:
        print("  service-managers:")
        for manager in managers:
            label = manager["role"] or "service"
            if manager.get("service_id"):
                label = f"{label}:{manager['service_id']}"
            print(f"    {label}: {manager['state']}")
    else:
        print("  service-managers: none")
    required = "yes" if report["teardown_required"] else "no"
    print(f"  teardown-required: {required}")


def qfw_dirsvc_start(argv):
    parser = argparse.ArgumentParser(prog="qfw-dirsvc-start")
    parser.add_argument("--site-config")
    parser.add_argument("--run-dir")
    parser.add_argument("--name")
    parser.add_argument("--host")
    parser.add_argument("--listen-port", type=int)
    parser.add_argument("--telnet-port", type=int, default=8091)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--pid-file")
    parser.add_argument("--ready-file")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    use_site_defaults = bool(
        args.site_config or not (args.name and args.listen_port is not None))
    site_path = (
        qfw_config.resolve_site_config(args.site_config)
        if use_site_defaults else None
    )
    site = _load_optional_site(args.site_config) if use_site_defaults else {}
    site_dir = qfw_config.site_directory_config(
        site, site_config_path=site_path) if site else {}
    site_host, site_port = _split_endpoint(site_dir.get("endpoint", ""))
    dirsvc_name = args.name or site_dir.get("name", "qfw-dirsvc")
    dirsvc_host = args.host or site_host or socket.gethostname()
    dirsvc_port = (
        args.listen_port if args.listen_port is not None else
        site_dir.get("listen_port") or site_port
    )
    if not dirsvc_port:
        dirsvc_port = 8090
    startup_timeout = (
        args.timeout if args.timeout is not None else
        int(site_dir.get("connect_timeout_seconds", 40))
    )
    endpoint = f"{dirsvc_host}:{dirsvc_port}"

    run_dir = _service_run_dir(args.run_dir, dirsvc_name)
    log_dir = run_dir / "logs"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    pid_file = Path(args.pid_file) if args.pid_file else run_dir / "pid"
    ready_file = (
        Path(args.ready_file) if args.ready_file else run_dir / "ready.json")
    env = os.environ.copy()
    env.update({
        "DEFW_AGENT_NAME": dirsvc_name,
        "DEFW_LISTEN_PORT": str(dirsvc_port),
        "DEFW_TELNET_PORT": str(args.telnet_port),
        "DEFW_ONLY_LOAD_MODULE": "svc_dirsvc",
        "DEFW_LOAD_NO_INIT": "",
        "DEFW_SHELL_TYPE": "daemon",
        "DEFW_AGENT_TYPE": "dirsvc",
        "DEFW_PARENT_HOSTNAME": dirsvc_host,
        "DEFW_PARENT_PORT": str(dirsvc_port),
        "DEFW_PARENT_NAME": dirsvc_name,
        "DEFW_LOG_LEVEL": os.environ.get("DEFW_LOG_LEVEL", "error"),
        "DEFW_DISABLE_DIRSVC": "yes",
        "DEFW_LOG_DIR": str(log_dir),
    })
    if args.site_config:
        env["QFW_SITE_CONFIG"] = str(qfw_config.resolve_site_config(
            args.site_config))
    return _start_defw_owned_process(
        dirsvc_name,
        env,
        pid_file,
        ready_file,
        startup_timeout,
        args.background,
        args.dry_run,
        {
            "role": "dirsvc",
            "name": dirsvc_name,
            "endpoint": endpoint,
            "startup_timeout": startup_timeout,
        },
        lambda: _tcp_endpoint_ready(endpoint),
    )


def qfw_service_start(argv):
    parser = argparse.ArgumentParser(prog="qfw-service-start")
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--site-config")
    parser.add_argument("--module")
    parser.add_argument("--load-modules")
    parser.add_argument("--operation-mode", default="long-running")
    parser.add_argument("--run-dir")
    parser.add_argument("--listen-port", type=int, default=8290)
    parser.add_argument("--telnet-port", type=int, default=8291)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--pid-file")
    parser.add_argument("--ready-file")
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    service_id = args.service_id
    run_dir = _service_run_dir(args.run_dir, service_id)
    log_dir = run_dir / "logs"
    service_ready_file = run_dir / "service-ready.json"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    service_ready_file.unlink(missing_ok=True)
    pid_file = Path(args.pid_file) if args.pid_file else run_dir / "pid"
    ready_file = (
        Path(args.ready_file) if args.ready_file else run_dir / "ready.json")
    site_config_path = qfw_config.resolve_site_config(args.site_config)
    site = _load_optional_site(args.site_config)
    local_directory_endpoint = os.environ.get("QFW_LOCAL_DIRSVC_ENDPOINT")
    if local_directory_endpoint:
        site_dir = {
            "name": os.environ.get(
                "QFW_LOCAL_DIRSVC_NAME", "qfw-local-dirsvc"),
            "endpoint": local_directory_endpoint,
            "connect_timeout_seconds": int(
                os.environ.get("QFW_DIRSVC_CONNECT_TIMEOUT_SECONDS", 300)),
        }
    else:
        site_dir = qfw_config.site_directory(
            site, site_config_path=site_config_path) if site else {}
    site_service = qfw_config.site_service_config(
        site, site_config_path=site_config_path) if site else {}
    service, service_config_path = _resolve_service(args, site_service)
    startup_timeout = (
        args.timeout if args.timeout is not None else
        int(site_dir.get("connect_timeout_seconds", 40))
    )
    module = args.module or service.get("module")
    if not module:
        raise SystemExit("qfw-service-start requires --module or a manifest entry")
    load_modules = args.load_modules or service.get("load-modules") or module
    operation_mode = service.get("operation-mode", args.operation_mode)
    register = qfw_config.bool_config(
        service.get("register-with-dirsvc"),
        default=operation_mode != "direct",
    )
    direct_fallback = qfw_config.bool_config(
        service.get("direct-endpoint-fallback"),
        default=False,
    )
    dirsvc_endpoint = (
        service.get("dirsvc-endpoint") or
        os.environ.get("QFW_LOCAL_DIRSVC_ENDPOINT") or
        site_dir.get("endpoint", "")
    )
    dirsvc_host, dirsvc_port = _split_endpoint(dirsvc_endpoint)
    dirsvc_name = (
        service.get("dirsvc-name") or
        os.environ.get("QFW_LOCAL_DIRSVC_NAME") or
        site_dir.get("name", "qfw-site-dirsvc")
    )
    allocation = _allocation_context_from_env()
    service_scope = os.environ.get("QFW_SERVICE_SCOPE", "site").strip().lower()
    if service_scope == "allocation-local":
        target = (
            os.environ.get("QFW_LOCAL_SERVICE_TARGET") or
            _resolve_node_policy(service.get("target"), allocation) or
            ""
        )
    else:
        target = str(service.get("host", ""))
    service_host = str(service.get(
        "bind-host", service.get("host", target or "127.0.0.1")))
    service_port = int(service.get("listen-port", args.listen_port))
    assigned_hosts = _resolve_host_policy(
        service.get("assigned-hosts"), allocation)
    env = os.environ.copy()
    env.update({
        "DEFW_AGENT_NAME": service_id,
        "DEFW_LISTEN_PORT": str(service_port),
        "DEFW_TELNET_PORT": str(service.get("telnet-port", args.telnet_port)),
        "DEFW_ONLY_LOAD_MODULE": load_modules,
        "DEFW_LOAD_NO_INIT": "",
        "DEFW_SHELL_TYPE": "daemon",
        "DEFW_AGENT_TYPE": service.get("agent-type", "service"),
        "DEFW_PARENT_HOSTNAME": dirsvc_host,
        "DEFW_PARENT_PORT": str(dirsvc_port),
        "DEFW_PARENT_NAME": dirsvc_name,
        "DEFW_LOG_LEVEL": service.get("log-level", "error"),
        "DEFW_DISABLE_DIRSVC": "no" if register else "yes",
        "DEFW_LOG_DIR": str(log_dir),
        "DEFW_PY_LOGLEVEL": "debug,DEFW_ALL",
        "QFW_QPM_OPERATION_MODE": operation_mode,
        "QFW_QPM_SERVICE_ID": service_id,
        "QFW_QPM_SERVICE_MODULE": module,
        "QFW_QPM_REGISTER_WITH_DIRSVC": "yes" if register else "no",
        "QFW_QPM_DIRECT_ENDPOINT_FALLBACK": (
            "yes" if direct_fallback else "no"),
        "QFW_STARTUP_TIMEOUT": str(startup_timeout),
        "QFW_SERVICE_READY_FILE": str(service_ready_file),
    })
    if service_config_path is not None:
        env["QFW_SERVICE_CONFIG"] = str(service_config_path)
    if target:
        env["QFW_LOCAL_SERVICE_TARGET"] = target
    if assigned_hosts:
        env["QFW_SERVICE_ASSIGNED_HOSTS"] = assigned_hosts
        assigned_hosts_env = service.get("assigned-hosts-env")
        if assigned_hosts_env:
            env[str(assigned_hosts_env)] = assigned_hosts
    if site_dir.get("endpoint"):
        env["QFW_SITE_DIRSVC_ENDPOINTS"] = site_dir["endpoint"]
    device_access_config = site_service.get("device_access_config")
    env.pop("QFW_DEVICE_ACCESS_CFG", None)
    if device_access_config:
        env["QFW_DEVICE_ACCESS_CFG"] = str(qfw_config.resolve_path(
            device_access_config))
    elif service.get("device-id"):
        raise SystemExit(
            "device-backed services require "
            "service.device-access-config in site.yaml")
    if service.get("device-id"):
        env["QFW_QPU_DEVICE_ID"] = str(service["device-id"])
    if service.get("direct-qpm-endpoint"):
        env["QFW_DIRECT_QPM_ENDPOINT"] = str(service["direct-qpm-endpoint"])
    env["QFW_SITE_CONFIG"] = str(site_config_path)
    return _start_defw_owned_process(
        service_id,
        env,
        pid_file,
        ready_file,
        startup_timeout,
        args.background,
        args.dry_run,
        {
            "role": "service",
            "service_id": service_id,
            "module": module,
            "endpoint": f"{service_host}:{service_port}",
            "dirsvc_name": dirsvc_name,
            "dirsvc_endpoint": dirsvc_endpoint,
            "startup_timeout": startup_timeout,
            "register_with_dirsvc": register,
            "service_ready_file": str(service_ready_file),
        },
        lambda: _service_ready(
            service_id,
            f"{service_host}:{service_port}",
            register,
            dirsvc_endpoint,
            service_ready_file,
        ),
    )


def _start_job_local_services(state):
    local_config = state["local_services"]
    env = os.environ.copy()
    env.update(state["environment"])
    local_timeout = _directory_timeout(state, "allocation-local")
    allocation = _allocation_context_from_env(env)
    _publish_allocation_environment(env, allocation)
    _publish_allocation_environment(state["environment"], allocation)
    managers = state.setdefault("service_managers", [])
    lifecycle_root = Path(state["run_dir"]) / "service-plane"
    directory_info = state["environment"].get(
        "QFW_DIRECTORY_SERVICE_INFO", "")

    if qfw_config.bool_config(local_config.get("start-dirsvc"), False):
        directory_run_dir = lifecycle_root / "directory"
        directory_state = qfw_service_plane.start_role(
            "directory",
            run_dir=directory_run_dir,
            site_config=state["site_config"],
            runtime_config=state["runtime_config"],
            scope="application",
            timeout=local_timeout,
            allocation=allocation,
        )
        managers.append({
            "owner": "application",
            "role": "directory",
            "run_dir": str(directory_run_dir),
        })
        state["service_managers"] = managers
        _adopt_managed_directory(state, directory_state)
        directory_info = state["environment"][
            "QFW_DIRECTORY_SERVICE_INFO"]
        qfw_config.write_state(state)
        qfw_config.write_env_file(state)

    if qfw_config.bool_config(local_config.get("start-qpm"), False):
        if not directory_info:
            raise RuntimeError(
                "application QPM startup requires a managed or configured "
                "directory-service connection record")
        manifest_path = Path(state["service_manifest"])
        services = qfw_config.load_service_manifest(manifest_path)
        selected = qfw_config.selected_service_names(local_config, services)
        launches = []
        for service_id in selected:
            service_run_dir = lifecycle_root / "qpm" / service_id
            service_state = qfw_service_plane.start_role(
                "qpm",
                run_dir=service_run_dir,
                site_config=state["site_config"],
                runtime_config=state["runtime_config"],
                scope="application",
                service_id=service_id,
                directory_service_info=directory_info,
                timeout=local_timeout,
                allocation=allocation,
            )
            managers.append({
                "owner": "application",
                "role": "qpm",
                "service_id": service_id,
                "run_dir": str(service_run_dir),
            })
            launches.append(dict(service_state["services"][0]))
            state["service_managers"] = managers
            state["local_service_launches"] = launches
            qfw_config.write_state(state)
        state["environment"]["QFW_QPM_SERVICE_IDS"] = ",".join(selected)
        if launches:
            state["environment"]["QFW_DVM_URI_PATH"] = str(
                Path(managers[-1]["run_dir"]) / "prte_dvm" / "dvm-uri")

    state["service_managers"] = managers
    qfw_config.write_state(state)
    qfw_config.write_env_file(state)


def _adopt_managed_directory(state, directory_state):
    directory = directory_state["directory"]
    endpoint = directory["endpoint"]
    host, port = _split_endpoint(endpoint)
    connection_file = directory["connection_file"]
    local = state.get("local_dirsvc") or {}
    local.update({
        "name": directory["name"],
        "host": host,
        "port": port,
        "telnet_port": directory["telnet_port"],
        "endpoint": endpoint,
        "connection_file": connection_file,
    })
    state["local_dirsvc"] = local
    state["environment"].update({
        "QFW_DIRECTORY_SERVICE_INFO": connection_file,
        "QFW_LOCAL_DIRSVC_ENDPOINT": endpoint,
        "QFW_LOCAL_DIRSVC_NAME": directory["name"],
        "DEFW_PARENT_HOSTNAME": host,
        "DEFW_PARENT_PORT": str(port),
        "DEFW_PARENT_NAME": directory["name"],
        "DEFW_DISABLE_DIRSVC": "no",
    })
    for requirement in state.get("directory_requirements") or []:
        if requirement.get("scope") != "allocation-local":
            continue
        requirement.update({
            "endpoint": endpoint,
            "name": directory["name"],
        })


def _target_launch_command(command, allocation, target):
    command = list(command)
    mode = allocation["mode"]
    if mode == "heterogeneous":
        return [
            "srun",
            "--het-group=1",
            "--nodes=1",
            "--ntasks=1",
            "--nodelist", str(target),
            *command,
        ]
    if mode == "slurm":
        return [
            "srun",
            "--nodes=1",
            "--ntasks=1",
            "--nodelist", str(target),
            *command,
        ]
    return command


def _application_launch_command(command, allocation, launch_options=None):
    command = list(command)
    mode = allocation["mode"]
    launch_options = launch_options or {}
    if mode == "heterogeneous":
        srun = ["srun"]
        het_group = launch_options.get("het_group")
        if het_group is None:
            het_group = "0"
        if str(het_group):
            srun.append(f"--het-group={het_group}")
        srun.extend(_application_srun_options(launch_options))
        return [*srun, *command]
    if mode == "slurm":
        return ["srun", *_application_srun_options(launch_options), *command]
    return command


def _configure_application_defw_environment(env, state=None):
    if state and not env.get("DEFW_LOG_DIR"):
        env["DEFW_LOG_DIR"] = str(
            Path(state["run_dir"]) / "application" / "logs")
    parent = _application_directory_parent(env)
    if parent is None:
        return
    parent_host, parent_port, parent_name = parent
    env["DEFW_DISABLE_DIRSVC"] = "no"
    env.setdefault("DEFW_AGENT_TYPE", "agent")
    env.setdefault("DEFW_SHELL_TYPE", "cmdline")
    env.setdefault("DEFW_PARENT_HOSTNAME", parent_host)
    env.setdefault("DEFW_PARENT_PORT", str(parent_port))
    env.setdefault("DEFW_PARENT_NAME", parent_name)
    parent_host = env.get("DEFW_PARENT_HOSTNAME", "")
    parent_addr = env.get("DEFW_PARENT_ADDR", "")
    if parent_host and parent_addr in {"", "0.0.0.0", "None"}:
        env["DEFW_PARENT_ADDR"] = _resolve_host_address(parent_host)
    if _int_value(env.get("DEFW_LISTEN_PORT"), 0) <= 0:
        env["DEFW_LISTEN_PORT"] = str(_free_tcp_port(""))


def _application_srun_options(launch_options):
    srun = []
    if launch_options.get("nodes"):
        srun.extend(["--nodes", str(launch_options["nodes"])])
    if launch_options.get("ntasks"):
        srun.extend(["--ntasks", str(launch_options["ntasks"])])
    if launch_options.get("nodelist"):
        srun.extend(["--nodelist", str(launch_options["nodelist"])])
    if launch_options.get("exclusive"):
        srun.append("--exclusive")
    return srun


def _application_directory_parent(env):
    local_endpoint = env.get("QFW_LOCAL_DIRSVC_ENDPOINT", "")
    if local_endpoint:
        host, port = _split_endpoint(local_endpoint)
        return (
            env.get("DEFW_PARENT_HOSTNAME") or host,
            env.get("DEFW_PARENT_PORT") or port,
            env.get("DEFW_PARENT_NAME") or
            env.get("QFW_LOCAL_DIRSVC_NAME", "qfw-local-dirsvc"),
        )

    site_endpoints = _split_configured_endpoints(
        env.get("QFW_SITE_DIRSVC_ENDPOINTS", ""))
    if not site_endpoints:
        return None
    host, port = _split_endpoint(site_endpoints[0])
    return (
        host,
        port,
        env.get("QFW_SITE_DIRSVC_NAME", "qfw-site-dirsvc"),
    )


def _split_configured_endpoints(value):
    if not value:
        return []
    return [
        item.strip()
        for item in str(value).replace(";", ",").split(",")
        if item.strip()
    ]


def _resolve_host_address(host):
    try:
        return socket.gethostbyname(host)
    except OSError:
        return host


def _free_tcp_port(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _int_value(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _start_defw_owned_process(name, env, pid_file, ready_file, timeout,
                              background, dry_run, ready_payload, ready_probe):
    if dry_run or env.get("QFW_STARTUP_DRY_RUN") == "1":
        pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")
        _write_ready(ready_file, ready_payload)
        return 0

    defw_python = _command_path("defw-python", env=env)
    stdout_log = _open_process_log(env, name, "stdout")
    stderr_log = _open_process_log(env, name, "stderr")
    try:
        process = subprocess.Popen(
            [str(defw_python), "-d", "-x"],
            env=env,
            start_new_session=True,
            stdout=stdout_log,
            stderr=stderr_log,
        )
    finally:
        stdout_log.close()
        stderr_log.close()
    pid_file.write_text(f"{process.pid}\n", encoding="utf-8")
    try:
        _wait_process_ready(process, timeout, name, ready_probe)
        _write_ready(ready_file, ready_payload)
        if background:
            return 0
        return _wait_foreground(process, pid_file, ready_file)
    except Exception:
        _terminate_process(process.pid)
        pid_file.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)
        raise


def _wait_foreground(process, pid_file, ready_file):
    terminating = {"active": False}

    def terminate(_signum, _frame):
        terminating["active"] = True
        _terminate_process(process.pid)

    old_int = signal.signal(signal.SIGINT, terminate)
    old_term = signal.signal(signal.SIGTERM, terminate)
    try:
        return process.wait()
    finally:
        signal.signal(signal.SIGINT, old_int)
        signal.signal(signal.SIGTERM, old_term)
        if terminating["active"]:
            _terminate_process(process.pid)
        pid_file.unlink(missing_ok=True)
        ready_file.unlink(missing_ok=True)


def _open_process_log(env, name, stream_name):
    log_dir = Path(env.get("DEFW_LOG_DIR") or _default_process_log_dir())
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in name
    )
    return (log_dir / f"{safe_name}.{stream_name}.log").open(
        "a",
        encoding="utf-8",
    )


def _default_process_log_dir():
    return qfw_config.qfw_run_base_dir() / "logs"


def _wait_process_ready(process, timeout, name, ready_probe):
    deadline = time.monotonic() + max(0, timeout)
    last_error = None
    while True:
        returncode = process.poll()
        if returncode is not None:
            raise RuntimeError(
                f"{name} exited during startup with status {returncode}")
        try:
            if ready_probe():
                return
        except Exception as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            detail = f": {last_error}" if last_error else ""
            raise TimeoutError(
                f"{name} readiness timed out after {timeout} seconds{detail}")
        time.sleep(0.1)


def _write_ready(path, payload):
    data = dict(payload)
    data["ready"] = True
    data["timestamp_ns"] = time.time_ns()
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.write("\n")


def _wait_required_directories(state):
    for requirement in state.get("directory_requirements") or []:
        endpoint = requirement["endpoint"]
        timeout = int(requirement.get("connect_timeout_seconds", 300))
        name = requirement.get("name") or requirement.get("scope") or endpoint
        _wait_for_ready(
            f"directory {name} at {endpoint}",
            timeout,
            lambda endpoint=endpoint: _tcp_endpoint_ready(endpoint),
        )


def _wait_for_ready(name, timeout, probe):
    deadline = time.monotonic() + max(0, timeout)
    last_error = None
    while True:
        try:
            if probe():
                return
        except Exception as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            detail = f": {last_error}" if last_error else ""
            raise TimeoutError(
                f"{name} readiness timed out after {timeout} seconds{detail}")
        time.sleep(0.1)


def _directory_timeout(state, scope):
    for requirement in state.get("directory_requirements") or []:
        if requirement.get("scope") == scope:
            return int(requirement.get("connect_timeout_seconds", 300))
    return 300


def _cleanup_job_processes(state, env=None, allocation=None,
                           report_errors=True):
    errors = []
    try:
        env, allocation = _cleanup_context_from_state(
            state, env=env, allocation=allocation)
    except Exception as exc:
        errors.append(str(exc))
        if report_errors:
            print(
                "errors while cleaning partially started services: " +
                "; ".join(errors),
                file=sys.stderr,
            )
        return errors
    for process in reversed(state.get("processes") or []):
        if process.get("owner") != "job":
            continue
        try:
            if process.get("role") == "prte-dvm":
                _cleanup_prte(process, env=env, allocation=allocation)
            elif process.get("pid") is not None:
                _cleanup_recorded_process(process, env, allocation)
        except Exception as exc:
            errors.append(str(exc))
    if errors and report_errors:
        print(
            "errors while cleaning partially started services: " +
            "; ".join(errors),
            file=sys.stderr,
        )
    return errors


def _cleanup_application_service_managers(state):
    errors = []
    for manager in reversed(state.get("service_managers") or []):
        if manager.get("owner") != "application":
            continue
        try:
            qfw_service_plane.stop(manager["run_dir"])
        except Exception as exc:
            role = manager.get("role", "service")
            service_id = manager.get("service_id")
            label = f"{role}:{service_id}" if service_id else role
            errors.append(f"{label}: {exc}")
    return errors


def _cleanup_context_from_state(state, env=None, allocation=None):
    cleanup_env = os.environ.copy()
    if env:
        cleanup_env.update(env)
    cleanup_env.update(state.get("environment") or {})
    if allocation is None:
        allocation = _allocation_context_from_env(cleanup_env)
    _publish_allocation_environment(cleanup_env, allocation)
    return cleanup_env, allocation


def _cleanup_recorded_process(process, env, allocation):
    pid = int(process["pid"])
    target = process.get("target")
    if _should_launch_on_target(allocation, target):
        _terminate_process_on_target(pid, target, env, allocation)
        return
    _terminate_process(pid)


def _cleanup_prte(process, env=None, allocation=None):
    env = env or os.environ.copy()
    allocation = allocation or {
        "mode": process.get("allocation_mode", "local"),
    }
    targets = _process_targets(process, allocation)
    launch_target = targets[0] if targets else None
    uri_path = process.get("uri_path")
    if uri_path:
        uri = Path(uri_path)
        if uri.exists():
            _run_cleanup_command(
                ["pterm", "--dvm", f"file:{uri}"],
                env,
                allocation,
                launch_target,
            )
        if _should_launch_on_any_target(allocation, targets):
            for target in targets:
                _terminate_prte_by_uri(uri, target, env, allocation)
        shutil.rmtree(str(uri.parent), ignore_errors=True)
    if not qfw_config.bool_config(process.get("force_cleanup"), False):
        return
    cleanup_targets = targets or [None]
    if _should_launch_on_any_target(allocation, targets):
        for target in cleanup_targets:
            for name in ("prte", "prted"):
                _run_cleanup_command(
                    ["pkill", "-9", name],
                    env,
                    allocation,
                    target,
                )
        return
    for name in ("prte", "prted"):
        _run_cleanup_command(
            ["pkill", "-9", name],
            env,
            allocation,
            None,
        )


def _service_ready(service_id, service_endpoint, register, dirsvc_endpoint,
                   service_ready_file=None):
    if _ready_file_ready(service_ready_file):
        return True
    if not register:
        return _service_endpoint_ready(service_endpoint)
    return False


def _ready_file_ready(path):
    if not path:
        return False
    ready_path = Path(path)
    if not ready_path.exists():
        return False
    try:
        with ready_path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get("ready"))


def _directory_endpoint_ready(endpoint):
    records = _directory_query(endpoint)
    if records is not None:
        return True
    return False


def _tcp_endpoint_ready(endpoint):
    host, port = _split_endpoint(endpoint)
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _directory_service_registered(endpoint, service_id):
    records = _directory_query(endpoint, service_id=service_id)
    if records is None:
        return False
    for record in records:
        if _record_service_id(record) == service_id:
            return True
    return False


def _directory_query(endpoint, service_id=None):
    try:
        client = _directory_client(endpoint)
        if service_id and hasattr(client, "resolve_services"):
            return _as_list(client.resolve_services(service_id=service_id))
        if service_id and hasattr(client, "resolve_service"):
            return _as_list(client.resolve_service(service_id=service_id))
        if hasattr(client, "query_directory"):
            return _as_list(client.query_directory())
        if hasattr(client, "query"):
            return _as_list(client.query())
        if hasattr(client, "resolve_services"):
            return _as_list(client.resolve_services(service_id="__qfw_ready__"))
        if hasattr(client, "resolve_service"):
            return _as_list(client.resolve_service(service_id="__qfw_ready__"))
    except Exception:
        return None
    return None


def _directory_client(endpoint):
    import defw

    if hasattr(defw, "connect_to_directory"):
        return defw.connect_to_directory(endpoint)
    if hasattr(defw, "connect_to_binding"):
        return defw.connect_to_binding(_directory_binding_record(endpoint))
    if hasattr(defw, "connect_to_endpoint"):
        return defw.connect_to_endpoint(endpoint, _directory_api_binding())
    raise RuntimeError("DEFw does not expose a directory client connector")


def _service_endpoint_ready(endpoint):
    return _defw_endpoint_ready(endpoint) is True


def _defw_endpoint_ready(endpoint):
    try:
        result = subprocess.run(
            [sys.executable or "python3", "-c", DEFW_ENDPOINT_READY_PROBE,
             str(endpoint)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=DEFW_ENDPOINT_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _defw_endpoint_ready_unbounded(endpoint):
    try:
        client = _endpoint_client(endpoint)
        if hasattr(client, "is_ready"):
            status = client.is_ready()
            if isinstance(status, dict):
                return bool(status.get("ready"))
            return bool(status)
        if hasattr(client, "ready"):
            ready = client.ready
            return bool(ready() if callable(ready) else ready)
        return False
    except Exception:
        return False


def _endpoint_client(endpoint):
    import defw

    if hasattr(defw, "connect_to_endpoint"):
        return defw.connect_to_endpoint(endpoint)
    if hasattr(defw, "connect_to_binding"):
        return defw.connect_to_binding(_endpoint_binding_record(endpoint))
    raise RuntimeError("DEFw does not expose an endpoint connector")


def _endpoint_binding_record(endpoint):
    endpoint_record = _endpoint_record(endpoint, default_name="qfw-service")
    return {
        "service_record": {
            "service_id": f"service:{endpoint}",
            "service_name": "QFwService",
            "service_type": "qfw.service",
            "runtime_id": endpoint_record["runtime_id"],
            "endpoint": endpoint_record,
            "selector": {},
            "properties": {},
        },
        "selected_binding": {
            "binding_name": "readiness",
            "client_module": "api_qpm_execution",
            "client_class": "QPMExecution",
            "service_module": "",
            "service_class": "",
            "version": 1,
        },
    }


def _directory_binding_record(endpoint):
    endpoint_record = _endpoint_record(endpoint, default_name="dirsvc")
    return {
        "service_record": {
            "service_id": f"dirsvc:{endpoint}",
            "service_name": "DEFwDirSvc",
            "service_type": "defw.dirsvc",
            "runtime_id": endpoint_record["runtime_id"],
            "endpoint": endpoint_record,
            "selector": {
                "resources": ["DEFwDirSvc"],
                "aliases": ["dirsvc", "directory"],
            },
            "properties": {},
        },
        "selected_binding": _directory_api_binding(),
    }


def _directory_api_binding():
    return {
        "binding_name": "directory",
        "client_module": "api_dirsvc",
        "client_class": "DEFwDirSvc",
        "service_module": "svc_dirsvc.svc_dirsvc",
        "service_class": "DEFwDirSvc",
        "version": 1,
    }


def _endpoint_record(endpoint, default_name=None):
    host, port = _split_endpoint(endpoint)
    return {
        "address": host,
        "listen_port": port,
        "pid": 0,
        "node_name": default_name or host,
        "hostname": host,
        "runtime_id": f"{host}:{port}",
    }


def _record_service_id(record):
    if not isinstance(record, dict):
        return None
    service = record.get("service_record")
    if isinstance(service, dict):
        return service.get("service_id")
    return record.get("service_id")


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _resolve_service(args, site_service):
    if args.module:
        manifest = os.environ.get("QFW_LOCAL_SERVICE_CONFIG", "").strip()
        return ({
            "name": args.service_id,
            "module": args.module,
            "load-modules": args.load_modules or args.module,
            "operation-mode": args.operation_mode,
        }, qfw_config.resolve_path(manifest) if manifest else None)

    scope = os.environ.get("QFW_SERVICE_SCOPE", "site").strip().lower()
    if scope == "allocation-local":
        manifest = os.environ.get("QFW_LOCAL_SERVICE_CONFIG", "").strip()
        if not manifest:
            raise ValueError(
                "allocation-local service startup requires prepared QFw "
                "runtime state")
    elif scope == "site":
        manifest = site_service.get("manifest")
        if not manifest:
            raise ValueError(
                "site service startup requires service.manifest in site.yaml")
    else:
        raise ValueError(f"unsupported QFw service scope: {scope}")

    manifest_path = qfw_config.resolve_path(manifest)
    services = qfw_config.load_service_manifest(manifest_path)
    return qfw_config.service_by_name(services, args.service_id), manifest_path


def _allocation_context_from_env(env=None):
    env = env or os.environ
    mode = env.get("QFW_ALLOCATION_MODE", "").strip().lower()
    group0_value = env.get("QFW_GROUP_0_NODELIST", "")
    group1_value = env.get("QFW_GROUP_1_NODELIST", "")
    groups_value = env.get("QFW_GROUPS", "")
    if not mode:
        if any(key.startswith("SLURM_JOB_NODELIST_HET_GROUP_")
               for key in env):
            mode = "heterogeneous"
        elif env.get("SLURM_JOB_NODELIST"):
            mode = "slurm"
        else:
            mode = "local"
    if mode == "heterogeneous" and (not group0_value or not group1_value):
        group0_value = env.get("SLURM_JOB_NODELIST_HET_GROUP_0", group0_value)
        group1_value = env.get("SLURM_JOB_NODELIST_HET_GROUP_1", group1_value)
    elif mode == "slurm" and (not group0_value or not group1_value):
        nodelist = env.get("SLURM_JOB_NODELIST", socket.gethostname())
        group0_value = group0_value or nodelist
        group1_value = group1_value or nodelist
    elif mode == "local":
        host = socket.gethostname()
        group0_value = group0_value or host
        group1_value = group1_value or host
    group0 = _expand_host_list(group0_value)
    group1 = _expand_host_list(group1_value)
    if not group0 or not group1:
        raise ValueError(
            "local-services require allocation groups 0 and 1; "
            f"got group0={group0_value!r} group1={group1_value!r}"
        )
    if not groups_value:
        groups_value = f"GROUP_0={group0_value}:GROUP_1={group1_value}"
    return {
        "mode": mode,
        "group0": group0,
        "group1": group1,
        "group0_nodelist": group0_value,
        "group1_nodelist": group1_value,
        "groups": groups_value,
    }


def _publish_allocation_environment(env, allocation):
    env["QFW_ALLOCATION_MODE"] = allocation["mode"]
    env["QFW_GROUP_0_NODELIST"] = allocation["group0_nodelist"]
    env["QFW_GROUP_1_NODELIST"] = allocation["group1_nodelist"]
    env["QFW_GROUPS"] = allocation["groups"]


def _expand_host_list(value):
    if not value:
        return []
    try:
        from defw_util import expand_host_list

        return list(expand_host_list(value))
    except Exception:
        return _expand_simple_host_list(str(value))


def _expand_simple_host_list(value):
    hosts = []
    for item in str(value).split(","):
        item = item.strip()
        if not item:
            continue
        if "[" not in item or "]" not in item:
            hosts.append(item)
            continue
        prefix, rest = item.split("[", 1)
        body, suffix = rest.split("]", 1)
        for part in body.split(","):
            if "-" not in part:
                hosts.append(f"{prefix}{part}{suffix}")
                continue
            start, end = part.split("-", 1)
            width = max(len(start), len(end))
            for number in range(int(start), int(end) + 1):
                hosts.append(f"{prefix}{number:0{width}d}{suffix}")
    return hosts


def _resolve_node_policy(policy, allocation):
    if not policy or policy == "group1-head":
        return allocation["group1"][0]
    if policy == "group0-head":
        return allocation["group0"][0]
    if policy == "local":
        return socket.gethostname()
    return str(policy)


def _resolve_host_policy(policy, allocation):
    if not policy:
        return ""
    if policy == "group1":
        return ",".join(allocation["group1"])
    if policy == "group0":
        return ",".join(allocation["group0"])
    if policy == "all":
        return ",".join(dict.fromkeys(
            allocation["group0"] + allocation["group1"]))
    return str(policy)


def _load_optional_site(site_config):
    if not site_config:
        resolved = qfw_config.resolve_site_config(None)
        if not resolved.exists():
            return {}
        return qfw_config.load_yaml(resolved)
    return qfw_config.load_yaml(qfw_config.resolve_site_config(site_config))


def _split_endpoint(endpoint):
    if isinstance(endpoint, dict):
        host = (
            endpoint.get("address") or
            endpoint.get("host") or
            endpoint.get("hostname") or
            "127.0.0.1"
        )
        port = (
            endpoint.get("listen_port") or
            endpoint.get("listen-port") or
            endpoint.get("port") or
            0
        )
        return str(host), int(port)
    if not endpoint:
        return "127.0.0.1", 0
    value = str(endpoint).strip()
    if "://" in value:
        from urllib.parse import urlparse

        parsed = urlparse(value)
        return parsed.hostname or "127.0.0.1", int(parsed.port or 0)
    if ":" not in value:
        return value, 0
    host, port = value.rsplit(":", 1)
    return host, int(port)


def _service_run_dir(run_dir, name):
    if run_dir:
        return Path(run_dir).expanduser().resolve() / "services" / name
    return qfw_config.qfw_run_base_dir() / "services" / name


def _command_path(name, env=None):
    env = env or os.environ
    bin_path = env.get("QFW_BIN_PATH")
    candidate = (
        Path(bin_path).expanduser().resolve() / name
        if bin_path else
        qfw_config.qfw_bin_path() / name
    )
    if candidate.exists():
        return candidate
    found = shutil.which(name, path=env.get("PATH"))
    if found:
        return Path(found)
    raise FileNotFoundError(f"unable to find QFw command: {name}")


def _run_checked(command, env):
    result = subprocess.run(command, env=env)
    if result.returncode:
        raise RuntimeError(
            f"command failed with {result.returncode}: {' '.join(command)}")


_REMOTE_TERMINATE_PROCESS = r"""
import os
import signal
import sys
import time

pid = int(sys.argv[1])

def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

def signal_process(sig):
    delivered = False
    try:
        os.killpg(pid, sig)
        delivered = True
    except ProcessLookupError:
        return False
    except OSError:
        pass
    try:
        os.kill(pid, sig)
        delivered = True
    except ProcessLookupError:
        return False
    except OSError:
        pass
    return delivered

if not signal_process(signal.SIGTERM):
    sys.exit(0)

deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    if not alive(pid):
        sys.exit(0)
    time.sleep(0.1)

signal_process(signal.SIGKILL)
deadline = time.monotonic() + 2
while time.monotonic() < deadline:
    if not alive(pid):
        sys.exit(0)
    time.sleep(0.1)

sys.exit(1 if alive(pid) else 0)
"""


_REMOTE_TERMINATE_PRTE_BY_URI = r"""
import os
import signal
import sys
import time

needle = sys.argv[1]

def proc_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as stream:
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""

def is_prte(pid, cmdline):
    if pid == os.getpid() or not cmdline:
        return False
    argv0 = cmdline.split("\0", 1)[0]
    name = os.path.basename(argv0)
    if name not in ("prte", "prted"):
        return False
    return needle in cmdline.replace("\0", " ")

def collect():
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        if is_prte(pid, proc_cmdline(pid)):
            pids.append(pid)
    return pids

def signal_pid(pid, sig):
    try:
        os.killpg(pid, sig)
    except (ProcessLookupError, OSError):
        pass
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, OSError):
        pass

pids = collect()
for pid in pids:
    signal_pid(pid, signal.SIGTERM)

deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    if not collect():
        sys.exit(0)
    time.sleep(0.1)

for pid in collect():
    signal_pid(pid, signal.SIGKILL)

deadline = time.monotonic() + 2
while time.monotonic() < deadline:
    if not collect():
        sys.exit(0)
    time.sleep(0.1)

sys.exit(1 if collect() else 0)
"""


def _process_targets(process, allocation):
    targets = list(process.get("targets") or [])
    if not targets and process.get("target"):
        targets.append(process["target"])
    if not targets and allocation.get("mode") in {"heterogeneous", "slurm"}:
        group1 = allocation.get("group1") or []
        if group1:
            targets.append(group1[0])
    return targets


def _should_launch_on_target(allocation, target):
    return bool(
        target and allocation.get("mode") in {"heterogeneous", "slurm"})


def _should_launch_on_any_target(allocation, targets):
    return bool(
        targets and allocation.get("mode") in {"heterogeneous", "slurm"})


def _run_cleanup_command(command, env, allocation, target):
    command = list(command)
    if _should_launch_on_target(allocation, target):
        command = _target_launch_command(command, allocation, target)
    return subprocess.run(
        command,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _terminate_process_on_target(pid, target, env, allocation):
    result = _run_cleanup_command(
        [sys.executable or "python3", "-c", _REMOTE_TERMINATE_PROCESS,
         str(pid)],
        env,
        allocation,
        target,
    )
    if result.returncode:
        raise RuntimeError(
            f"failed to terminate pid {pid} on {target}: "
            f"status {result.returncode}")


def _terminate_prte_by_uri(uri, target, env, allocation):
    result = _run_cleanup_command(
        [sys.executable or "python3", "-c", _REMOTE_TERMINATE_PRTE_BY_URI,
         str(uri)],
        env,
        allocation,
        target,
    )
    if result.returncode:
        raise RuntimeError(
            f"failed to terminate PRTE processes for {uri} on {target}: "
            f"status {result.returncode}")


def _terminate_process(pid):
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return


if __name__ == "__main__":
    raise SystemExit(main())
