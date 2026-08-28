import argparse
import json
import os
import shutil
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
}
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
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"{command}: {exc}", file=sys.stderr)
        return 1
    return 2


def qfw_setup(argv):
    parser = argparse.ArgumentParser(prog="qfw-setup")
    parser.add_argument("--site-config")
    parser.add_argument("--runtime-config")
    parser.add_argument("--profile")
    parser.add_argument("--service-id")
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
        service_id=args.service_id,
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
        runtime_service_ids = []
        for service_name in selected:
            service_id = qfw_config.application_service_id(
                service_name, state["run_id"])
            runtime_service_ids.append(service_id)
            service_run_dir = lifecycle_root / "qpm" / service_name
            service_state = qfw_service_plane.start_role(
                "qpm",
                run_dir=service_run_dir,
                site_config=state["site_config"],
                runtime_config=state["runtime_config"],
                scope="application",
                service_id=service_id,
                manifest_service_name=service_name,
                directory_service_info=directory_info,
                timeout=local_timeout,
                allocation=allocation,
            )
            managers.append({
                "owner": "application",
                "role": "qpm",
                "service_id": service_id,
                "manifest_service_name": service_name,
                "run_dir": str(service_run_dir),
            })
            launches.append(dict(service_state["services"][0]))
            state["service_managers"] = managers
            state["local_service_launches"] = launches
            qfw_config.write_state(state)
        state["environment"]["QFW_QPM_SERVICE_IDS"] = ",".join(
            runtime_service_ids)
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


def _tcp_endpoint_ready(endpoint):
    host, port = _split_endpoint(endpoint)
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


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


if __name__ == "__main__":
    raise SystemExit(main())
