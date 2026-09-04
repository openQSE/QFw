"""Manage a persistent QFw directory, DVM, and QPM service plane."""

import argparse
import contextlib
import fcntl
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import config as qfw_config
from . import environment_modules as qfw_environment_modules


SCHEMA = "qfw-service-plane-v1"
STATE_NAME = "service-plane.json"
ENV_NAME = "qfw-service-env.sh"
TERMINATE_TIMEOUT_SECONDS = 10


class ServicePlaneError(RuntimeError):
    """Report a service-plane configuration or lifecycle failure."""


class _StoreOnce(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f"{option_string} may be specified only once")
        setattr(namespace, self.dest, values)


def directory_service_main(argv=None):
    return _role_main("directory", argv)


def qpm_service_main(argv=None):
    return _role_main("qpm", argv)


def start_role(role, *, run_dir, site_config=None, runtime_config=None,
               profile=None, scope="site", service_id=None,
               manifest_service_name=None,
               directory_service_info=None, node=None, timeout=120,
               poll_interval=2.0, dry_run=False, allocation=None):
    """Start one lifecycle role through the shared service-plane engine."""
    if role not in {"directory", "qpm"}:
        raise ValueError(f"unsupported service-plane role: {role}")
    if role == "qpm" and not service_id:
        raise ValueError("qpm lifecycle requires a service ID")
    if role == "directory" and service_id:
        raise ValueError("directory lifecycle does not accept a service ID")
    args = argparse.Namespace(
        action="start",
        run_dir=str(run_dir),
        site_config=str(site_config) if site_config else None,
        runtime_config=str(runtime_config) if runtime_config else None,
        profile=profile,
        scope=scope,
        node=node,
        timeout=timeout,
        poll_interval=poll_interval,
        dry_run=bool(dry_run),
        component_mode=role,
        directory_node=None,
        qpm_node=None,
        directory_service_info=(
            str(directory_service_info)
            if directory_service_info else None
        ),
        service_id=str(service_id) if service_id else None,
        manifest_service_name=(
            str(manifest_service_name) if manifest_service_name else None),
        allocation=allocation,
    )
    return start(args)


def _role_main(role, argv=None):
    program = "qfw-dir-svc" if role == "directory" else "qfw-qpm-svc"
    parser = _role_argument_parser(program, role)
    args = parser.parse_args(argv)
    try:
        if args.action == "start":
            state = start(args)
            _print_state(state)
            return 0
        if args.action == "run":
            return run(args)
        if args.action == "status":
            state = status(args.run_dir)
            _print_state(state)
            return 0 if state["state"] == "ready" else 1
        if args.action == "stop":
            state = stop(args.run_dir)
            _print_state(state)
            return 0
    except (OSError, ServicePlaneError, ValueError) as exc:
        print(f"{program}: {exc}", file=sys.stderr)
        return 1
    return 2


def _role_argument_parser(program, role):
    parser = argparse.ArgumentParser(prog=program)
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("start", "run"):
        command = actions.add_parser(name)
        command.add_argument("--run-dir", required=True)
        command.add_argument("--site-config")
        command.add_argument("--runtime-config")
        command.add_argument("--profile")
        command.add_argument(
            "--scope", choices=("application", "site"), default="site")
        command.add_argument("--node")
        command.add_argument("--timeout", type=int, default=120)
        command.add_argument("--poll-interval", type=float, default=2.0)
        command.add_argument("--dry-run", action="store_true")
        command.set_defaults(
            component_mode=role,
            directory_node=None,
            qpm_node=None,
            directory_service_info=None,
            service_id=None,
            manifest_service_name=None,
        )
        if role == "directory":
            command.set_defaults(directory_node=None)
        else:
            command.add_argument(
                "--service-id", required=True, action=_StoreOnce)
            command.add_argument("--manifest-service-name")
            command.add_argument("--directory-service-info")
    for name in ("status", "stop"):
        command = actions.add_parser(name)
        command.add_argument("--run-dir", required=True)
    return parser


def start(args):
    run_dir = _resolve_run_dir(args.run_dir)
    with _state_lock(run_dir):
        previous = _read_state(run_dir, required=False)
        if previous and previous.get("state") != "stopped":
            raise ServicePlaneError(
                f"service plane already has state {previous.get('state')!r}: "
                f"{_state_path(run_dir)}"
            )

        plan = _resolve_plan(args, run_dir)
        state = _new_state(plan, args.dry_run)
        _write_state(state)
        _write_environment(state)

        try:
            service_environment = os.environ.copy()
            if plan["components"]["qpm"] and not args.dry_run:
                service_environment = qfw_environment_modules.load_for_service(
                    plan["services"][0]["manifest"], service_environment)
            if plan["components"]["prte"]:
                _start_prte(state, args.timeout, service_environment)
            if plan["components"]["directory"]:
                _publish_directory_connection(state, ready=False)
                _start_directory(state, args.timeout)
                _publish_directory_connection(state, ready=True)
            if plan["components"]["qpm"]:
                for service in plan["services"]:
                    _start_qpm(
                        state, service, args.timeout, service_environment)
            state["state"] = "ready"
            state["ready_at_ns"] = time.time_ns()
            state["updated_at_ns"] = state["ready_at_ns"]
            _write_state(state)
            return state
        except Exception as exc:
            state["state"] = "error"
            state["error"] = str(exc)
            state["updated_at_ns"] = time.time_ns()
            _stop_components(state)
            _write_state(state)
            raise


def status(run_dir):
    run_dir = _resolve_run_dir(run_dir)
    with _state_lock(run_dir):
        state = _read_state(run_dir)
        if state.get("state") == "stopped":
            return state

        expected = _expected_component_keys(state)
        all_ready = set(state.get("components", {})) == expected
        for component in state.get("components", {}).values():
            ready = _component_ready(component, state)
            component["ready"] = ready
            component["state"] = "ready" if ready else "not-ready"
            component["checked_at_ns"] = time.time_ns()
            all_ready = all_ready and ready
        directory_component = state.get("components", {}).get("directory")
        if directory_component:
            _publish_directory_connection(
                state, ready=directory_component.get("ready", False))
        state["state"] = "ready" if all_ready else "degraded"
        state["updated_at_ns"] = time.time_ns()
        _write_state(state)
        return state


def _expected_component_keys(state):
    configured = state.get("configuration", {}).get("components", {})
    expected = set()
    if configured.get("prte"):
        expected.add("prte-dvm")
    if configured.get("directory"):
        expected.add("directory")
    if configured.get("qpm"):
        expected.update(
            f"qpm:{service_id}"
            for service_id in state["configuration"].get("service_ids", [])
        )
    return expected


def stop(run_dir):
    run_dir = _resolve_run_dir(run_dir)
    with _state_lock(run_dir):
        state = _read_state(run_dir)
        if state.get("state") == "stopped":
            return state
        errors = _stop_components(state)
        state["state"] = "error" if errors else "stopped"
        if errors:
            state["error"] = "; ".join(errors)
        else:
            state.pop("error", None)
            state["stopped_at_ns"] = time.time_ns()
        state["updated_at_ns"] = time.time_ns()
        _write_state(state)
        if errors:
            raise ServicePlaneError(state["error"])
        return state


def run(args):
    state = start(args)
    with _state_lock(args.run_dir):
        state = _read_state(args.run_dir)
        state["manager_pid"] = os.getpid()
        state["manager_mode"] = "foreground"
        state["updated_at_ns"] = time.time_ns()
        _write_state(state)
    _print_state(state)

    stopping = {"requested": False}

    def request_stop(_signum, _frame):
        stopping["requested"] = True

    old_term = signal.signal(signal.SIGTERM, request_stop)
    old_int = signal.signal(signal.SIGINT, request_stop)
    try:
        while not stopping["requested"]:
            time.sleep(max(0.1, args.poll_interval))
            current = status(args.run_dir)
            if current["state"] != "ready":
                current["state"] = "error"
                current["error"] = "an owned service-plane component stopped"
                current["updated_at_ns"] = time.time_ns()
                _write_state(current)
                _stop_components(current)
                _write_state(current)
                return 1
        stop(args.run_dir)
        return 0
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)


def _resolve_plan(args, run_dir):
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.poll_interval <= 0:
        raise ValueError("--poll-interval must be positive")

    site_path = qfw_config.resolve_site_config(args.site_config)
    site = qfw_config.load_yaml(site_path)
    prefixes = qfw_config.site_install_prefixes(site)
    runtime_path = qfw_config.resolve_runtime_config(
        explicit=args.runtime_config,
        profile=args.profile,
        qfw_prefix_override=prefixes["qfw_prefix"],
    )
    runtime = qfw_config.load_yaml(runtime_path)
    local = qfw_config.local_services(runtime)
    allocation = getattr(args, "allocation", None) or _allocation_context()
    component_mode = args.component_mode
    if component_mode not in {"directory", "qpm"}:
        raise ServicePlaneError(
            f"unsupported service-plane role: {component_mode}")
    node = getattr(args, "node", None)
    if args.scope == "application" and node:
        raise ServicePlaneError(
            "--node is not accepted in application scope; group1-head is "
            "selected automatically")
    directory_node = (
        getattr(args, "directory_node", None) or
        (node if component_mode == "directory" else None)
    )
    qpm_node = (
        getattr(args, "qpm_node", None) or
        (node if component_mode == "qpm" else None)
    )
    for selected_node in {directory_node, qpm_node} - {None}:
        if allocation["mode"] == "local" and selected_node not in {
                "127.0.0.1", "localhost", socket.gethostname()}:
            raise ServicePlaneError(
                f"cannot launch on {selected_node!r} outside a Slurm "
                "allocation; run the manager on that node or invoke it "
                "inside an allocation")

    component_config = local or {}
    if args.scope == "application" and not local:
        raise ServicePlaneError(
            "application scope requires local-services in the runtime config"
        )
    if component_mode == "directory":
        components = {"prte": False, "directory": True, "qpm": False}
    elif component_mode == "qpm":
        prte_configured = "start-prte" in component_config
        components = {
            "prte": qfw_config.bool_config(
                component_config.get("start-prte"), False),
            "directory": False,
            "qpm": True,
        }
    if components["directory"]:
        directory = _resolve_directory(
            site, site_path, local, allocation, args.scope,
            directory_node, args.dry_run, run_dir,
        )
    elif components["qpm"]:
        directory = _resolve_existing_directory(
            site, site_path, args.scope,
            getattr(args, "directory_service_info", None),
        )
    else:
        directory = {}

    if args.scope == "application":
        manifest_path = qfw_config.service_manifest_path(
            local,
            qfw_prefix_value=prefixes["qfw_prefix"],
        )
    else:
        site_service = qfw_config.site_service_config(
            site, site_config_path=site_path)
        manifest_path = site_service.get("manifest")

    services = []
    if components["qpm"]:
        if not manifest_path:
            raise ServicePlaneError("QPM startup requires a service manifest")
        manifest_services = qfw_config.load_service_manifest(manifest_path)
        selected = _selected_service_ids(
            getattr(args, "manifest_service_name", None) or
            getattr(args, "service_id", None),
            local,
            manifest_services)
        default_target = (
            socket.gethostname() if args.scope == "site"
            else directory.get("target", "")
        )
        services = _resolve_services(
            selected, manifest_services, local, allocation, args, qpm_node,
            default_target,
            runtime_service_id=getattr(args, "service_id", None))
        if component_mode == "qpm":
            service_requires_prte = any(
                service["provider_launch"].get("type") == "mpi"
                and qfw_config.bool_config(
                    service["provider_launch"].get("use-dvm"), True)
                for service in services
            )
            if not prte_configured:
                components["prte"] = service_requires_prte
            elif components["prte"]:
                components["prte"] = service_requires_prte
    if components["qpm"] and not directory.get("endpoint"):
        raise ServicePlaneError(
            "QPM startup requires a directory endpoint"
        )

    dvm_nodes = _dvm_nodes(
        local, services, allocation, directory, args.scope)
    return {
        "run_dir": str(run_dir),
        "scope": args.scope,
        "owner": "application" if args.scope == "application" else "site",
        "site_config": str(site_path),
        "runtime_config": str(runtime_path),
        "service_manifest": str(manifest_path) if manifest_path else "",
        "components": components,
        "directory": directory,
        "services": services,
        "dvm": {
            "nodes": dvm_nodes,
            "uri_path": str(run_dir / "prte_dvm" / "dvm-uri"),
            "launch_node": dvm_nodes[0] if dvm_nodes else "",
        },
        "allocation": allocation,
    }


def _selected_service_ids(explicit, local, manifest_services):
    if explicit:
        selected = [str(explicit)]
    elif local.get("services") is not None:
        selected = qfw_config.selected_service_names(local, manifest_services)
    elif len(manifest_services) == 1:
        selected = [str(manifest_services[0]["name"])]
    else:
        names = ", ".join(
            str(service.get("name", "")) for service in manifest_services)
        raise ServicePlaneError(
            "multiple QPM services are available; select one with "
            f"--service-id: {names}"
        )
    if not selected:
        raise ServicePlaneError("no QPM service was selected")
    if len(selected) != 1:
        raise ServicePlaneError(
            "one service-plane instance manages exactly one QPM; select one "
            "service with --service-id")
    return selected


def _resolve_services(selected, manifest_services, local, allocation, args,
                      qpm_node, default_target, runtime_service_id=None):
    listen_port = int(local.get("service-listen-port-base", 8290))
    telnet_port = int(local.get("service-telnet-port-base", 8291))
    stride = int(local.get("service-port-stride", 100))
    resolved = []
    manifest_indexes = {
        str(service.get("name", "")): index
        for index, service in enumerate(manifest_services)
    }
    for service_name in selected:
        service = qfw_config.service_by_name(manifest_services, service_name)
        index = manifest_indexes[service_name]
        if args.scope == "application":
            target = allocation["group1"][0]
        else:
            target = str(
                service.get("host") or qpm_node or default_target)
        target = target or socket.gethostname()
        assigned_hosts = _resolve_host_policy(
            service.get("assigned-hosts"), allocation)
        if not assigned_hosts:
            assigned_hosts = target
        service_listen = int(service.get(
            "listen-port", listen_port + index * stride))
        service_telnet = int(service.get(
            "telnet-port", telnet_port + index * stride))
        resolved.append({
            "service_id": runtime_service_id or service_name,
            "manifest_service_name": service_name,
            "module": service.get("module", ""),
            "target": target,
            "assigned_hosts": assigned_hosts,
            "assigned_hosts_env": service.get("assigned-hosts-env", ""),
            "listen_port": service_listen,
            "telnet_port": service_telnet,
            "provider_launch": service.get("provider-launch") or {},
            "environment_modules": list(
                service.get("environment-modules") or []),
            "required_executables": list(
                service.get("required-executables") or []),
            "manifest": service,
        })
    return resolved


def _resolve_directory(site, site_path, local, allocation, scope,
                       override_node, dry_run, run_dir):
    if scope == "application":
        name, host, port, _endpoint = qfw_config.allocate_local_endpoint(
            local, dry_run=dry_run)
        telnet_port = qfw_config.allocate_local_telnet_port(
            local, host, port, dry_run=dry_run)
        host = allocation["group1"][0]
        return {
            "name": name,
            "host": host,
            "port": port,
            "telnet_port": telnet_port,
            "endpoint": f"{host}:{port}",
            "target": host,
            "connection_file": str(run_dir / "directory-service.json"),
            "connect_timeout_seconds": int(
                (local.get("dirsvc") or {}).get(
                    "connect-timeout-seconds",
                    local.get("connect-timeout-seconds", 300),
                )
            ),
        }

    configured = qfw_config.site_directory_config(site, site_path)
    if not configured:
        raise ServicePlaneError(
            "site-owned directory startup requires directory-service "
            "configuration")
    if not configured.get("connection_file"):
        raise ServicePlaneError(
            "qfw-dir-svc requires directory-service.connection-file so it "
            "can publish its resolved endpoint")
    host = override_node or socket.gethostname()
    port = configured["listen_port"]
    return {
        "name": configured["name"],
        "host": host,
        "port": port,
        "telnet_port": port + 1,
        "endpoint": f"{host}:{port}",
        "target": host,
        "connection_file": str(configured.get("connection_file") or ""),
        "connect_timeout_seconds": configured["connect_timeout_seconds"],
    }


def _resolve_existing_directory(site, site_path, scope, connection_file):
    if scope == "site":
        configured = qfw_config.site_directory(
            site, site_config_path=site_path,
            connection_file=connection_file)
    else:
        selected = (
            connection_file or
            os.environ.get("QFW_DIRECTORY_SERVICE_INFO")
        )
        if not selected:
            raise ServicePlaneError(
                "application QPM startup requires --directory-service-info")
        configured = qfw_config.read_directory_service_connection(selected)
    host, port = _split_endpoint(configured["endpoint"])
    return {
        "name": configured["name"],
        "host": host,
        "port": port,
        "telnet_port": port + 1,
        "endpoint": configured["endpoint"],
        "target": host,
        "connection_file": str(configured.get("connection_file") or ""),
        "connect_timeout_seconds": configured.get(
            "connect_timeout_seconds", 300),
    }


def _dvm_nodes(local, services, allocation, directory, scope):
    if scope == "application":
        return list(allocation["group1"])
    configured = (local.get("prte") or {}).get("hosts")
    if configured:
        return _expand_hosts(qfw_config.expand_config_value(configured))
    nodes = []
    for service in services:
        nodes.extend(_expand_hosts(qfw_config.expand_config_value(
            service.get("assigned_hosts", ""))))
    nodes = list(dict.fromkeys(nodes))
    if nodes:
        return nodes
    if allocation.get("group1"):
        return allocation["group1"]
    target = directory.get("target")
    return [target] if target else []


def _new_state(plan, dry_run):
    now = time.time_ns()
    return {
        "schema": SCHEMA,
        "instance_id": uuid.uuid4().hex,
        "state": "starting",
        "run_dir": plan["run_dir"],
        "scope": plan["scope"],
        "owner": plan["owner"],
        "dry_run": bool(dry_run),
        "configuration": {
            "site_config": plan["site_config"],
            "runtime_config": plan["runtime_config"],
            "service_manifest": plan["service_manifest"],
            "components": plan["components"],
            "service_ids": [
                service["service_id"] for service in plan["services"]],
        },
        "directory": plan["directory"],
        "services": [
            {key: value for key, value in service.items() if key != "manifest"}
            for service in plan["services"]
        ],
        "dvm": plan["dvm"],
        "allocation": plan["allocation"],
        "components": {},
        "created_at_ns": now,
        "updated_at_ns": now,
    }


def _start_prte(state, timeout, service_environment=None):
    dvm = state["dvm"]
    uri_path = Path(dvm["uri_path"])
    uri_path.parent.mkdir(parents=True, exist_ok=True)
    uri_path.unlink(missing_ok=True)
    record = {
        "role": "prte-dvm",
        "owner": state["owner"],
        "state": "starting",
        "ready": False,
        "node": dvm["launch_node"],
        "nodes": dvm["nodes"],
        "uri_path": str(uri_path),
    }
    state["components"]["prte-dvm"] = record
    _write_state(state)
    if state["dry_run"]:
        uri_path.write_text("dry-run-dvm-uri\n", encoding="utf-8")
    else:
        process_environment = dict(service_environment or os.environ)
        command = [
            _command_path("prte", env=process_environment),
            "--host", ",".join(f"{host}:*" for host in dvm["nodes"]),
            "--report-uri", str(uri_path),
        ]
        job_id = os.environ.get("QFW_JOB_ID") or os.environ.get("SLURM_JOB_ID")
        if job_id and str(job_id) != "-1":
            command.extend([
                "-x", f"SLURM_JOB_ID={job_id}",
                "-x", f"SLURM_JOBID={job_id}",
            ])
        command.extend(_prte_runtime_args(state["allocation"]))
        if os.geteuid() == 0:
            command.append("--allow-run-as-root")
        command.extend(["--keepalive", "0"])
        command.append("--daemonize")
        _run_on_node(dvm["launch_node"], command, process_environment,
                     state["allocation"])
        _wait_for(lambda: uri_path.exists() and uri_path.stat().st_size > 0,
                  timeout, f"PRTE DVM URI {uri_path}")
    record["state"] = "ready"
    record["ready"] = True
    record["ready_at_ns"] = time.time_ns()
    state["updated_at_ns"] = record["ready_at_ns"]
    _write_state(state)


def _start_directory(state, timeout):
    directory = state["directory"]
    name = directory["name"]
    component_dir = Path(state["run_dir"]) / "services" / name
    pid_file = component_dir / "pid"
    ready_file = component_dir / "ready.json"
    record = {
        "role": "directory",
        "name": name,
        "owner": state["owner"],
        "state": "starting",
        "ready": False,
        "node": directory["target"],
        "endpoint": directory["endpoint"],
        "connection_file": directory["connection_file"],
        "pid_file": str(pid_file),
        "ready_file": str(ready_file),
        "log_dir": str(component_dir / "logs"),
    }
    state["components"]["directory"] = record
    _write_state(state)
    if state["dry_run"]:
        _write_dry_run_component(record)
    else:
        command = [
            sys.executable or "python3",
            "-m", "qfw_runtime._process_launcher", "directory",
            "--background",
            "--site-config", state["configuration"]["site_config"],
            "--run-dir", state["run_dir"],
            "--name", name,
            "--host", directory["host"],
            "--listen-port", str(directory["port"]),
            "--telnet-port", str(directory["telnet_port"]),
            "--timeout", str(timeout),
            "--pid-file", str(pid_file),
            "--ready-file", str(ready_file),
        ]
        _run_on_node(directory["target"], command, os.environ.copy(),
                     state["allocation"])
        record["pid"] = _read_pid(pid_file)
    record["state"] = "ready"
    record["ready"] = True
    record["ready_at_ns"] = time.time_ns()
    state["updated_at_ns"] = record["ready_at_ns"]
    _write_state(state)


def _start_qpm(state, service, timeout, service_environment=None):
    service_id = service["service_id"]
    component_dir = Path(state["run_dir"]) / "services" / service_id
    pid_file = component_dir / "pid"
    ready_file = component_dir / "ready.json"
    record = {
        "role": "qpm",
        "service_id": service_id,
        "owner": state["owner"],
        "state": "starting",
        "ready": False,
        "node": service["target"],
        "assigned_nodes": _expand_hosts(service["assigned_hosts"]),
        "pid_file": str(pid_file),
        "ready_file": str(ready_file),
        "service_ready_file": str(component_dir / "service-ready.json"),
        "log_dir": str(component_dir / "logs"),
    }
    state["components"][f"qpm:{service_id}"] = record
    _write_state(state)
    if state["dry_run"]:
        _write_dry_run_component(record)
    else:
        env = dict(service_environment or os.environ)
        env["QFW_SERVICE_SCOPE"] = (
            "allocation-local" if state["scope"] == "application" else "site")
        env["QFW_DVM_URI_PATH"] = state["dvm"]["uri_path"]
        env["QFW_SERVICE_ASSIGNED_HOSTS"] = service["assigned_hosts"]
        if service.get("assigned_hosts_env"):
            env[service["assigned_hosts_env"]] = service["assigned_hosts"]
        directory = state["directory"]
        if state["scope"] == "application":
            env["QFW_LOCAL_SERVICE_CONFIG"] = (
                state["configuration"]["service_manifest"])
            env["QFW_LOCAL_SERVICE_TARGET"] = service["target"]
            env["QFW_LOCAL_DIRSVC_ENDPOINT"] = directory.get("endpoint", "")
            env["QFW_LOCAL_DIRSVC_NAME"] = directory.get("name", "")
        else:
            env["QFW_SITE_DIRSVC_ENDPOINTS"] = directory.get("endpoint", "")
            env["QFW_SITE_DIRSVC_NAME"] = directory.get("name", "")
        command = [
            sys.executable or "python3",
            "-m", "qfw_runtime._process_launcher", "qpm",
            "--background",
            "--run-dir", state["run_dir"],
            "--service-id", service_id,
            "--manifest-service-name", service["manifest_service_name"],
            "--site-config", state["configuration"]["site_config"],
            "--operation-mode", (
                "qfw-managed" if state["scope"] == "application"
                else "long-running"),
            "--listen-port", str(service["listen_port"]),
            "--telnet-port", str(service["telnet_port"]),
            "--timeout", str(timeout),
            "--pid-file", str(pid_file),
            "--ready-file", str(ready_file),
        ]
        _run_on_node(service["target"], command, env, state["allocation"])
        record["pid"] = _read_pid(pid_file)
    record["state"] = "ready"
    record["ready"] = True
    record["ready_at_ns"] = time.time_ns()
    state["updated_at_ns"] = record["ready_at_ns"]
    _write_state(state)


def _write_dry_run_component(record):
    ready_file = Path(record["ready_file"])
    ready_file.parent.mkdir(parents=True, exist_ok=True)
    ready_file.write_text(json.dumps({
        "ready": True,
        "role": record["role"],
        "timestamp_ns": time.time_ns(),
    }, sort_keys=True) + "\n", encoding="utf-8")
    record["pid"] = None


def _publish_directory_connection(state, ready):
    if state.get("dry_run"):
        return
    directory = state.get("directory") or {}
    selected = directory.get("connection_file")
    if not selected:
        return
    path = Path(selected)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            existing = {}
            try:
                with path.open("r", encoding="utf-8") as stream:
                    existing = json.load(stream)
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            if _directory_connection_active(existing) and existing.get(
                    "instance_id") != state.get("instance_id"):
                raise ServicePlaneError(
                    "directory-service connection file is owned by active "
                    f"instance {existing.get('instance_id')}: {path}")
            record = {
                "schema": qfw_config.DIRECTORY_SERVICE_CONNECTION_SCHEMA,
                "instance_id": state["instance_id"],
                "name": directory["name"],
                "endpoint": directory["endpoint"],
                "connect_timeout_seconds": directory.get(
                    "connect_timeout_seconds", 300),
                "ready": bool(ready),
                "updated_at_ns": time.time_ns(),
            }
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(record, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _directory_connection_active(record):
    if record.get("ready") is not True:
        return False
    endpoint = record.get("endpoint")
    if not endpoint:
        return True
    try:
        host, port = _split_endpoint(endpoint)
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except (OSError, TypeError, ValueError):
        return False


def _withdraw_directory_connection(state):
    if state.get("dry_run"):
        return
    directory = state.get("directory") or {}
    selected = directory.get("connection_file")
    if not selected:
        return
    path = Path(selected)
    try:
        with path.open("r", encoding="utf-8") as stream:
            record = json.load(stream)
    except (FileNotFoundError, json.JSONDecodeError):
        return
    if record.get("instance_id") != state.get("instance_id"):
        return
    _publish_directory_connection(state, ready=False)


def _component_ready(component, state):
    if component["role"] == "prte-dvm":
        path = Path(component["uri_path"])
        return path.exists() and path.stat().st_size > 0
    if state.get("dry_run"):
        return _ready_file(Path(component["ready_file"]))
    pid = component.get("pid")
    if pid is None:
        return False
    if not _pid_alive(pid, component.get("node"), state["allocation"]):
        return False
    return _ready_file(Path(component["ready_file"]))


def _stop_components(state):
    errors = []
    if state.get("configuration", {}).get("components", {}).get("directory"):
        try:
            _withdraw_directory_connection(state)
        except Exception as exc:
            errors.append(f"directory connection record: {exc}")
    components = list(state.get("components", {}).items())
    for key, component in reversed(components):
        try:
            if component["role"] == "prte-dvm":
                _stop_prte(component, state)
            elif not state.get("dry_run") and component.get("pid") is not None:
                _terminate_pid(
                    int(component["pid"]), component.get("node"),
                    state["allocation"])
            component["ready"] = False
            component["state"] = "stopped"
            component["stopped_at_ns"] = time.time_ns()
        except Exception as exc:
            component["state"] = "error"
            component["error"] = str(exc)
            errors.append(f"{key}: {exc}")
    return errors


def _stop_prte(component, state):
    uri_path = Path(component["uri_path"])
    if not uri_path.exists() or state.get("dry_run"):
        uri_path.unlink(missing_ok=True)
        return
    command = [_command_path("pterm"), "--dvm", f"file:{uri_path}"]
    _run_on_node(component.get("node"), command, os.environ.copy(),
                 state["allocation"])
    uri_path.unlink(missing_ok=True)


def _terminate_pid(pid, node, allocation):
    if allocation.get("mode") in {"slurm", "heterogeneous"}:
        script = (
            "import os,signal,sys,time\n"
            "pid=int(sys.argv[1])\n"
            "def alive():\n"
            "  try: os.kill(pid,0); return True\n"
            "  except ProcessLookupError: return False\n"
            "def send(sig):\n"
            "  try: os.killpg(pid,sig)\n"
            "  except (ProcessLookupError,PermissionError):\n"
            "    try: os.kill(pid,sig)\n"
            "    except ProcessLookupError: pass\n"
            "send(signal.SIGTERM)\n"
            f"deadline=time.monotonic()+{TERMINATE_TIMEOUT_SECONDS}\n"
            "while alive() and time.monotonic()<deadline: time.sleep(.1)\n"
            "if alive(): send(signal.SIGKILL)\n"
        )
        _run_on_node(node, ["python3", "-c", script, str(pid)],
                     os.environ.copy(), allocation)
        return
    _signal_local_pid(pid, signal.SIGTERM)
    deadline = time.monotonic() + TERMINATE_TIMEOUT_SECONDS
    while _local_pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if _local_pid_alive(pid):
        _signal_local_pid(pid, signal.SIGKILL)


def _signal_local_pid(pid, sig):
    delivered = False
    try:
        os.killpg(pid, sig)
        delivered = True
    except (ProcessLookupError, PermissionError):
        pass
    if delivered:
        return
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def _pid_alive(pid, node, allocation):
    if allocation.get("mode") not in {"slurm", "heterogeneous"}:
        return _local_pid_alive(pid)
    result = _run_on_node(
        node,
        ["python3", "-c", "import os,sys; os.kill(int(sys.argv[1]), 0)",
         str(pid)],
        os.environ.copy(),
        allocation,
        check=False,
    )
    return result.returncode == 0


def _local_pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _ready_file(path):
    try:
        with path.open("r", encoding="utf-8") as stream:
            return bool(json.load(stream).get("ready"))
    except (OSError, json.JSONDecodeError):
        return False


def _run_on_node(node, command, env, allocation, check=True):
    launch = list(command)
    mode = allocation.get("mode")
    if mode == "heterogeneous":
        group = _group_for_node(node, allocation)
        launch = [
            "srun", f"--het-group={group}", "--nodes=1", "--ntasks=1",
            "--nodelist", str(node), *launch,
        ]
    elif mode == "slurm":
        launch = [
            "srun", "--nodes=1", "--ntasks=1", "--nodelist", str(node),
            *launch,
        ]
    result = subprocess.run(launch, env=env)
    if check and result.returncode:
        raise ServicePlaneError(
            f"command failed with {result.returncode}: "
            + " ".join(shlex.quote(str(item)) for item in launch)
        )
    return result


def _group_for_node(node, allocation):
    if node in allocation.get("group1", []):
        return "1"
    if node in allocation.get("group0", []):
        return "0"
    raise ServicePlaneError(f"node is not in the heterogeneous allocation: {node}")


def _allocation_context():
    mode = os.environ.get("QFW_ALLOCATION_MODE", "").strip().lower()
    group0_value = os.environ.get("QFW_GROUP_0_NODELIST", "")
    group1_value = os.environ.get("QFW_GROUP_1_NODELIST", "")
    if not mode:
        if any(key.startswith("SLURM_JOB_NODELIST_HET_GROUP_") for key in os.environ):
            mode = "heterogeneous"
        elif os.environ.get("SLURM_JOB_NODELIST"):
            mode = "slurm"
        else:
            mode = "local"
    if mode == "heterogeneous":
        group0_value = group0_value or os.environ.get(
            "SLURM_JOB_NODELIST_HET_GROUP_0", "")
        group1_value = group1_value or os.environ.get(
            "SLURM_JOB_NODELIST_HET_GROUP_1", "")
    elif mode == "slurm":
        nodes = os.environ.get("SLURM_JOB_NODELIST", socket.gethostname())
        group0_value = group0_value or nodes
        group1_value = group1_value or nodes
    else:
        host = socket.gethostname()
        group0_value = group0_value or host
        group1_value = group1_value or host
    group0 = _expand_hosts(group0_value)
    group1 = _expand_hosts(group1_value)
    if not group0 or not group1:
        raise ServicePlaneError(
            "service-plane startup requires allocation groups 0 and 1"
        )
    return {
        "mode": mode,
        "group0": group0,
        "group1": group1,
        "group0_nodelist": group0_value,
        "group1_nodelist": group1_value,
    }


def _expand_hosts(value):
    if not value:
        return []
    try:
        from defw_util import expand_host_list
        return list(expand_host_list(value))
    except Exception:
        return [item.strip() for item in str(value).split(",") if item.strip()]


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
    policy = qfw_config.expand_config_value(policy)
    if policy == "group1":
        return ",".join(allocation["group1"])
    if policy == "group0":
        return ",".join(allocation["group0"])
    if policy == "all":
        return ",".join(dict.fromkeys(
            allocation["group0"] + allocation["group1"]))
    return str(policy)


def _split_endpoint(endpoint):
    value = str(endpoint)
    if "://" in value:
        from urllib.parse import urlparse
        parsed = urlparse(value)
        return parsed.hostname or "127.0.0.1", int(parsed.port or 0)
    host, port = value.rsplit(":", 1)
    return host, int(port)


def _prte_runtime_args(allocation):
    if allocation["mode"] == "heterogeneous":
        return [
            "--prtemca", "ras", "^slurm",
            "--prtemca", "plm", "slurm",
            "--prtemca", "plm_slurm_args", "--het-group 1",
        ]
    if allocation["mode"] == "slurm":
        return [
            "--prtemca", "ras", "^slurm",
            "--prtemca", "plm", "slurm",
        ]
    return []


def _command_path(name, env=None):
    env = env or os.environ
    bin_path = env.get("QFW_BIN_PATH")
    if bin_path:
        candidate = Path(bin_path).expanduser().resolve() / name
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name, path=env.get("PATH"))
    if found:
        return found
    raise ServicePlaneError(f"unable to find required command: {name}")


def _wait_for(probe, timeout, description):
    deadline = time.monotonic() + timeout
    while True:
        if probe():
            return
        if time.monotonic() >= deadline:
            raise ServicePlaneError(
                f"timed out after {timeout} seconds waiting for {description}")
        time.sleep(0.1)


def _read_pid(path):
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise ServicePlaneError(f"invalid or missing PID file: {path}") from exc


def _state_path(run_dir):
    return Path(run_dir) / "state" / STATE_NAME


def _resolve_run_dir(value):
    if value is None or not str(value).strip():
        raise ValueError("--run-dir must not be empty")
    return Path(value).expanduser().resolve()


def _read_state(run_dir, required=True):
    path = _state_path(run_dir)
    if not path.exists() and not required:
        return None
    try:
        with path.open("r", encoding="utf-8") as stream:
            state = json.load(stream)
    except FileNotFoundError as exc:
        raise ServicePlaneError(f"service-plane state not found: {path}") from exc
    if state.get("schema") != SCHEMA:
        raise ServicePlaneError(f"unsupported service-plane state: {path}")
    return state


def _write_state(state):
    path = _state_path(state["run_dir"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2, sort_keys=True)
        stream.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _write_environment(state):
    path = Path(state["run_dir"]) / ENV_NAME
    directory = state["directory"]
    values = {
        "QFW_SERVICE_PLANE_RUN_DIR": state["run_dir"],
        "QFW_SITE_CONFIG": state["configuration"]["site_config"],
        "QFW_RUNTIME_CONFIG": state["configuration"]["runtime_config"],
        "QFW_QPM_SERVICE_IDS": ",".join(
            state["configuration"]["service_ids"]),
    }
    if directory.get("connection_file"):
        values["QFW_DIRECTORY_SERVICE_INFO"] = directory["connection_file"]
    if state["scope"] == "site":
        values["QFW_SITE_DIRSVC_ENDPOINTS"] = directory.get("endpoint", "")
        values["QFW_SITE_DIRSVC_NAME"] = directory.get("name", "")
    else:
        values["QFW_LOCAL_DIRSVC_ENDPOINT"] = directory.get("endpoint", "")
        values["QFW_LOCAL_DIRSVC_NAME"] = directory.get("name", "")
        values["QFW_QPM_RESOLVER_SCOPE_ORDER"] = "allocation-local"
    with path.open("w", encoding="utf-8") as stream:
        for name, value in sorted(values.items()):
            stream.write(f"export {name}={shlex.quote(str(value))}\n")
    os.chmod(path, 0o640)


@contextlib.contextmanager
def _state_lock(run_dir):
    state_dir = Path(run_dir) / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    lock_path = state_dir / ".service-plane.lock"
    with lock_path.open("a+", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ServicePlaneError(
                f"another service-plane operation is active: {run_dir}") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _print_state(state):
    print(json.dumps(state, indent=2, sort_keys=True))


if __name__ == "__main__":
    role = os.environ.get("QFW_SERVICE_LIFECYCLE_ROLE")
    if role == "directory":
        raise SystemExit(directory_service_main())
    if role == "qpm":
        raise SystemExit(qpm_service_main())
    raise SystemExit(
        "set QFW_SERVICE_LIFECYCLE_ROLE to 'directory' or 'qpm'")
