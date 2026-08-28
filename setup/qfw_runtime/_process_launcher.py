"""Private remote-node launcher for QFw-owned DEFw processes."""

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
from util import device_access


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"directory", "qpm"}:
        print(
            "Usage: python -m qfw_runtime._process_launcher "
            "<directory|qpm> [args...]",
            file=sys.stderr,
        )
        return 2
    role = argv.pop(0)
    try:
        if role == "directory":
            return start_directory(argv)
        return start_qpm(argv)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"qfw-{role}-svc: {exc}", file=sys.stderr)
        return 1


def start_directory(argv):
    parser = argparse.ArgumentParser(prog="qfw-dir-svc")
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


def start_qpm(argv):
    parser = argparse.ArgumentParser(prog="qfw-qpm-svc")
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--site-config")
    parser.add_argument("--module")
    parser.add_argument("--load-modules")
    parser.add_argument(
        "--operation-mode",
        choices=("long-running", "qfw-managed"),
        default="long-running",
    )
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
        raise SystemExit("qfw-qpm-svc requires --module or a manifest entry")
    load_modules = args.load_modules or service.get("load-modules") or module
    credential_mode = str(service.get("credential-mode") or "").strip()
    operation_mode = service.get("operation-mode", args.operation_mode)
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
        "DEFW_DISABLE_DIRSVC": "no",
        "DEFW_LOG_DIR": str(log_dir),
        "DEFW_PY_LOGLEVEL": "debug,DEFW_ALL",
        "QFW_QPM_OPERATION_MODE": operation_mode,
        "QFW_QPM_SERVICE_ID": service_id,
        "QFW_QPM_SERVICE_MODULE": module,
        "QFW_QPM_CREDENTIAL_MODE": credential_mode,
        "QFW_QPM_RUN_DIR": str(run_dir),
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
    elif credential_mode == "required":
        raise SystemExit(
            "services with required credentials need "
            "service.device-access-config in site.yaml")
    if service.get("device-id"):
        env["QFW_QPU_DEVICE_ID"] = str(service["device-id"])
    if credential_mode == "required":
        try:
            device_access.validate_credential_configuration(
                env["QFW_DEVICE_ACCESS_CFG"], service["device-id"])
        except Exception as exc:
            raise SystemExit(str(exc)) from exc
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
            "register_with_dirsvc": True,
            "service_ready_file": str(service_ready_file),
        },
        lambda: _service_ready(service_ready_file),
    )

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

def _service_ready(service_ready_file=None):
    return _ready_file_ready(service_ready_file)


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


def _tcp_endpoint_ready(endpoint):
    host, port = _split_endpoint(endpoint)
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


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
