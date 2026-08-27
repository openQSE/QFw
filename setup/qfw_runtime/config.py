import json
import os
import re
import shlex
import socket
import tempfile
import uuid
from pathlib import Path

import yaml


SCOPE_ALIASES = {
    "local": "allocation-local",
    "allocation-local": "allocation-local",
    "site": "site",
    "direct": "direct",
}
CALLER_ENVIRONMENT_KEYS = (
    "PATH",
    "LD_LIBRARY_PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONNOUSERSITE",
    "PYTHONUSERBASE",
    "VIRTUAL_ENV",
    "VIRTUAL_ENV_PROMPT",
)
ENVIRONMENT_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
UNBRACED_ENVIRONMENT_REFERENCE = re.compile(
    r"\$([A-Za-z_][A-Za-z0-9_]*)")
UNSUPPORTED_PATH_PLACEHOLDERS = {
    "<prefix>": "${QFW_PREFIX}",
    "<qfw-prefix>": "${QFW_PREFIX}",
    "<defw-prefix>": "${DEFW_PREFIX}",
}
DIRECTORY_SERVICE_CONNECTION_SCHEMA = "qfw-directory-service-v1"


def _split_config_list(value):
    if not value:
        return []
    if isinstance(value, str):
        items = value.replace(";", ",").split(",")
    else:
        items = value
    return [str(item).strip() for item in items if str(item).strip()]


def _persist_caller_environment(environment):
    for name in CALLER_ENVIRONMENT_KEYS:
        value = os.environ.get(name)
        if value:
            environment[name] = value


def qfw_prefix():
    value = os.environ.get("QFW_PREFIX")
    if value:
        return Path(value).expanduser().resolve()
    source_path = Path(__file__).resolve()
    if source_path.parent.name == "qfw_runtime":
        setup_dir = source_path.parent.parent
        if setup_dir.name == "setup":
            return setup_dir.parent
    return Path.cwd().resolve()


def qfw_share_dir(prefix=None):
    if prefix is not None:
        return Path(prefix).expanduser().resolve() / "share" / "qfw"
    value = os.environ.get("QFW_SHARE_DIR")
    if value:
        return Path(value).expanduser().resolve()
    return qfw_prefix() / "share" / "qfw"


def qfw_bin_path(prefix=None):
    if prefix is not None:
        return Path(prefix).expanduser().resolve() / "bin"
    value = os.environ.get("QFW_BIN_PATH")
    if value:
        return Path(value).expanduser().resolve()
    return qfw_prefix() / "bin"


def qfw_run_base_dir():
    value = (
        os.environ.get("QFW_RUN_BASE_DIR") or
        os.path.join(tempfile.gettempdir(), "qfw-runs")
    )
    return Path(value).expanduser().resolve()


def load_yaml(path):
    path = Path(path).expanduser()
    with path.open("r", encoding="utf-8") as stream:
        data = yaml.safe_load(stream) or {}
    if not isinstance(data, dict):
        raise ValueError(f"configuration must be a mapping: {path}")
    return data


def expand_config_value(value):
    if value is None:
        return None
    text = str(value)

    for placeholder, replacement in UNSUPPORTED_PATH_PLACEHOLDERS.items():
        if placeholder in text:
            raise ValueError(
                f"unsupported configuration placeholder {placeholder!r}; "
                f"use {replacement}")

    unbraced = UNBRACED_ENVIRONMENT_REFERENCE.search(text)
    if unbraced:
        name = unbraced.group(1)
        raise ValueError(
            f"environment variable references must use braced form: "
            f"${{{name}}}")

    unmatched = ENVIRONMENT_REFERENCE.sub("", text)
    if "${" in unmatched:
        raise ValueError(
            f"invalid environment variable reference in configuration: "
            f"{text!r}")

    def replace_environment(match):
        name = match.group(1)
        if name not in os.environ:
            raise ValueError(
                f"configuration references unset environment variable: "
                f"${{{name}}}")
        selected = os.environ[name]
        if not selected:
            raise ValueError(
                f"configuration references empty environment variable: "
                f"${{{name}}}")
        return selected

    text = ENVIRONMENT_REFERENCE.sub(replace_environment, text)
    return os.path.expanduser(text)


def resolve_path(value, base=None):
    value = expand_config_value(value)
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute() and base is not None:
        path = Path(base) / path
    return path.expanduser().resolve()


def resolve_site_config(explicit=None):
    selected = (
        explicit or
        os.environ.get("QFW_SITE_CONFIG") or
        str(qfw_share_dir() / "config" / "site.yaml")
    )
    return resolve_path(selected)


def resolve_runtime_config(explicit=None, profile=None,
                           qfw_prefix_override=None):
    if explicit:
        return resolve_path(explicit)
    if profile:
        return (
            qfw_share_dir(qfw_prefix_override) /
            "config" / "runtime" / f"{profile}.yaml"
        )
    env_config = os.environ.get("QFW_RUNTIME_CONFIG")
    if env_config:
        return resolve_path(env_config)
    env_profile = os.environ.get("QFW_RUNTIME_PROFILE")
    if env_profile:
        return (
            qfw_share_dir(qfw_prefix_override) /
            "config" / "runtime" / f"{env_profile}.yaml"
        )
    return qfw_share_dir(qfw_prefix_override) / "config" / "runtime.yaml"


def site_install_prefixes(site_config):
    install = site_config.get("install") or {}
    if not isinstance(install, dict):
        raise ValueError("install must be a mapping")
    removed_keys = {
        "qfw_prefix": "qfw-prefix",
        "defw_prefix": "defw-prefix",
        "prefix": "qfw-prefix",
    }
    for key, replacement in removed_keys.items():
        if key in install:
            raise ValueError(
                f"unsupported install key {key!r}; use {replacement!r}")
    qfw_value = install.get("qfw-prefix")
    qfw_path = (
        resolve_path(qfw_value)
        if qfw_value is not None else
        qfw_prefix()
    )
    defw_value = install.get("defw-prefix")
    if defw_value is not None:
        defw_path = resolve_path(defw_value)
    else:
        source_defw = qfw_path / "DEFw"
        if source_defw.exists() and not (qfw_path / "bin" / "defwp").exists():
            defw_path = source_defw
        else:
            defw_path = qfw_path
    return {
        "qfw_prefix": qfw_path,
        "defw_prefix": defw_path,
    }


def site_directory_config(site_config, site_config_path=None):
    directory = site_config.get("directory-service") or {}
    if not directory:
        return {}
    if not isinstance(directory, dict):
        raise ValueError("directory-service must be a mapping")
    base = None
    if site_config_path is not None:
        base = Path(site_config_path).expanduser().resolve().parent
    connection_file = directory.get("connection-file")
    endpoint = str(directory.get("endpoint") or "").strip()
    if not connection_file and not endpoint:
        raise ValueError(
            "directory-service requires connection-file or a stable endpoint")
    configured_port = directory.get("listen-port")
    if configured_port is None and endpoint:
        try:
            configured_port = endpoint.rsplit(":", 1)[1]
        except (IndexError, ValueError) as exc:
            raise ValueError(
                f"invalid directory-service endpoint: {endpoint}") from exc
    listen_port = int(configured_port or 8090)
    if listen_port <= 0 or listen_port > 65535:
        raise ValueError(
            f"invalid directory-service listen port: {listen_port}")
    selected = {
        "name": str(directory.get("name", "qfw-site-dirsvc")),
        "listen_port": listen_port,
        "connect_timeout_seconds": int(
            directory.get("connect-timeout-seconds", 300)),
    }
    if connection_file:
        selected["connection_file"] = str(
            resolve_path(connection_file, base=base))
    if endpoint:
        selected["endpoint"] = endpoint
        selected["endpoints"] = [endpoint]
    return selected


def read_directory_service_connection(connection_file, defaults=None):
    path = resolve_path(connection_file)
    try:
        with path.open("r", encoding="utf-8") as stream:
            record = json.load(stream)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"directory-service connection record is not available: {path}"
        ) from exc
    if not isinstance(record, dict):
        raise ValueError(
            f"directory-service connection record must be a mapping: {path}")
    if record.get("schema") != DIRECTORY_SERVICE_CONNECTION_SCHEMA:
        raise ValueError(
            f"unsupported directory-service connection record: {path}")
    if record.get("ready") is not True:
        raise RuntimeError(f"directory service is not ready: {path}")
    endpoint = str(record.get("endpoint") or "").strip()
    name = str(record.get("name") or "").strip()
    if not endpoint or not name:
        raise ValueError(
            f"directory-service connection record lacks name or endpoint: "
            f"{path}")
    selected = dict(defaults or {})
    selected.update({
        "name": name,
        "endpoint": endpoint,
        "endpoints": [endpoint],
        "connection_file": str(path),
        "instance_id": str(record.get("instance_id") or ""),
    })
    selected.setdefault("connect_timeout_seconds", int(
        record.get("connect_timeout_seconds", 300)))
    return selected


def site_directory(site_config, site_config_path=None,
                   connection_file=None):
    configured = site_directory_config(site_config, site_config_path)
    endpoint_override = os.environ.get("QFW_SITE_DIRSVC_ENDPOINTS")
    if endpoint_override:
        endpoints = _split_config_list(endpoint_override)
        if endpoints:
            selected = dict(configured)
            selected.update({
                "name": os.environ.get(
                    "QFW_SITE_DIRSVC_NAME",
                    configured.get("name", "qfw-site-dirsvc"),
                ),
                "endpoint": endpoints[0],
                "endpoints": endpoints,
            })
            return selected
    if configured.get("endpoint"):
        return configured
    selected_file = (
        connection_file or
        os.environ.get("QFW_DIRECTORY_SERVICE_INFO") or
        configured.get("connection_file")
    )
    if not selected_file:
        return {}
    return read_directory_service_connection(
        selected_file, defaults=configured)


def site_service_config(site_config, site_config_path=None):
    service = site_config.get("service") or {}
    if not isinstance(service, dict):
        raise ValueError("service must be a mapping")
    removed_keys = {
        "service-manifest": "manifest",
        "service_manifest": "manifest",
        "device_access_config": "device-access-config",
    }
    for key, replacement in removed_keys.items():
        if key in service:
            raise ValueError(
                f"unsupported service key {key!r}; use {replacement!r}")
    base = None
    if site_config_path is not None:
        base = Path(site_config_path).expanduser().resolve().parent

    result = {}
    for canonical, key in {
            "manifest": "manifest",
            "device_access_config": "device-access-config",
    }.items():
        value = service.get(key)
        if value is None:
            continue
        result[canonical] = resolve_path(value, base=base)
    return result


def resolver_scope_order(runtime_config):
    resolver = runtime_config.get("resolver") or {}
    scopes = resolver.get("scope-order") or ["site"]
    return normalize_resolver_scope_order(scopes)


def normalize_resolver_scope_order(scopes):
    normalized = []
    for scope in _split_config_list(scopes):
        scope_name = SCOPE_ALIASES.get(str(scope).strip())
        if not scope_name:
            raise ValueError(f"unsupported resolver scope: {scope}")
        normalized.append(scope_name)
    return normalized


def directory_readiness_requirements(scope_order, site_dir, local_dir):
    requirements = []
    for scope in scope_order:
        if scope == "site":
            site_endpoints = site_dir.get("endpoints") or [
                site_dir.get("endpoint")]
            site_endpoints = [endpoint for endpoint in site_endpoints
                              if endpoint]
            if not site_endpoints:
                raise ValueError("site resolver scope requires a site directory endpoint")
            for endpoint in site_endpoints:
                requirements.append({
                    "scope": "site",
                    "name": site_dir.get("name", "qfw-site-dirsvc"),
                    "endpoint": endpoint,
                    "connect_timeout_seconds": int(
                        site_dir.get("connect_timeout_seconds", 300)),
                })
        elif scope == "allocation-local":
            if not local_dir.get("endpoint"):
                raise ValueError(
                    "local resolver scope requires a local directory endpoint")
            requirements.append({
                "scope": "allocation-local",
                "name": local_dir.get("name", "qfw-local-dirsvc"),
                "endpoint": local_dir["endpoint"],
                "connect_timeout_seconds": int(
                    local_dir.get("connect_timeout_seconds", 300)),
            })
    return requirements


def local_services(runtime_config):
    value = runtime_config.get("local-services") or {}
    if not isinstance(value, dict):
        raise ValueError("local-services must be a mapping")
    return value


def bool_config(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def allocate_local_endpoint(local_config, dry_run=False):
    dirsvc = local_config.get("dirsvc") or {}
    host = str(dirsvc.get("bind-host", "127.0.0.1"))
    configured_port = dirsvc.get("port", "auto")
    if str(configured_port).strip().lower() == "auto" and dry_run:
        port = 0
    elif str(configured_port).strip().lower() == "auto":
        port = _free_tcp_port(host)
    else:
        port = int(configured_port)
    name = str(dirsvc.get("name", "qfw-local-dirsvc"))
    return name, host, port, f"{host}:{port}"


def allocate_local_telnet_port(local_config, host, listen_port,
                               dry_run=False):
    dirsvc = local_config.get("dirsvc") or {}
    configured_port = dirsvc.get("telnet-port", "auto")
    if str(configured_port).strip().lower() == "auto" and dry_run:
        port = 0
    elif str(configured_port).strip().lower() == "auto":
        port = _free_tcp_port(host)
        while port == listen_port:
            port = _free_tcp_port(host)
    else:
        port = int(configured_port)
    if port == 0 and dry_run:
        return port
    if port <= 0 or port > 65535:
        raise ValueError(f"invalid local directory telnet port: {port}")
    if port == listen_port:
        raise ValueError("local directory listen and telnet ports must differ")
    return port


def service_manifest_path(local_config, qfw_prefix_value=None):
    manifest = local_config.get("service-manifest")
    if manifest is not None:
        return resolve_path(manifest)
    prefix = (
        Path(qfw_prefix_value).expanduser().resolve()
        if qfw_prefix_value is not None else qfw_prefix()
    )
    return prefix / "share" / "qfw" / "config" / "services" / \
        "local-services.yaml"


def selected_service_names(local_config, manifest_services):
    selected = local_config.get("services")
    if selected is None:
        return [str(item["name"]) for item in manifest_services
                if "name" in item]
    if isinstance(selected, str):
        return [selected]
    return [str(item) for item in selected]


def load_service_manifest(path):
    data = load_yaml(path)
    services = data.get("services") or []
    if not isinstance(services, list):
        raise ValueError(f"services must be a list: {path}")
    for index, service in enumerate(services):
        if not isinstance(service, dict):
            raise ValueError(
                f"service entry {index} must be a mapping: {path}")
        for key, replacement in {
                "listen_port": "listen-port",
                "telnet_port": "telnet-port",
        }.items():
            if key in service:
                raise ValueError(
                    f"unsupported service key {key!r}; use {replacement!r}")
        for key in ("environment-modules", "required-executables"):
            value = service.get(key)
            if value is None:
                continue
            if not isinstance(value, list):
                raise ValueError(
                    f"service {service.get('name', index)!r} {key} must be "
                    "a list")
            if any(not str(item).strip() for item in value):
                raise ValueError(
                    f"service {service.get('name', index)!r} {key} entries "
                    "must not be empty")
    return services


def prepare_run_state(site_config_path, runtime_config_path, site_config,
                      runtime_config, profile=None, run_id=None,
                      run_dir=None, dry_run=False, service_id=None):
    prefixes = site_install_prefixes(site_config)
    qfw_install_prefix = prefixes["qfw_prefix"]
    defw_install_prefix = prefixes["defw_prefix"]
    run_id = run_id or os.environ.get("QFW_RUN_ID") or uuid.uuid4().hex
    if run_dir:
        run_root = Path(run_dir).expanduser().resolve()
        run_base = run_root.parent
    else:
        run_base = qfw_run_base_dir()
        run_root = run_base / run_id
    log_dir = run_root / "logs"
    state_dir = run_root / "state"
    run_root.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    local_config = dict(local_services(runtime_config))
    if service_id:
        if not local_config:
            raise ValueError(
                "--service-id requires a runtime profile with local-services")
        local_config["services"] = [service_id]
    scope_order = resolver_scope_order(runtime_config)
    scope_override = os.environ.get("QFW_QPM_RESOLVER_SCOPE_ORDER")
    if scope_override:
        scope_order = normalize_resolver_scope_order(scope_override)
    site_dir = {}
    if "site" in scope_order or os.environ.get("QFW_SITE_DIRSVC_ENDPOINTS"):
        site_dir = site_directory(site_config, site_config_path)
    endpoint_override = os.environ.get("QFW_SITE_DIRSVC_ENDPOINTS")
    if endpoint_override:
        site_endpoints = _split_config_list(endpoint_override)
        if site_endpoints:
            site_dir = dict(site_dir)
            site_dir.setdefault(
                "name",
                os.environ.get("QFW_SITE_DIRSVC_NAME", "qfw-site-dirsvc"),
            )
            site_dir.setdefault(
                "connect_timeout_seconds",
                int(os.environ.get("QFW_DIRSVC_CONNECT_TIMEOUT_SECONDS", 300)),
            )
            site_dir["endpoint"] = site_endpoints[0]
            site_dir["endpoints"] = site_endpoints
    local_endpoint = ""
    local_name = ""
    local_port = None
    local_telnet_port = None
    local_host = ""
    manifest_path = None
    if local_config:
        local_name, local_host, local_port, local_endpoint = (
            allocate_local_endpoint(local_config, dry_run=dry_run))
        local_telnet_port = allocate_local_telnet_port(
            local_config,
            local_host,
            local_port,
            dry_run=dry_run,
        )
        manifest_path = service_manifest_path(
            local_config,
            qfw_prefix_value=qfw_install_prefix,
        )
    environment = {
        "QFW_PREFIX": str(qfw_install_prefix),
        "QFW_BIN_PATH": str(qfw_bin_path(qfw_install_prefix)),
        "QFW_LIBEXEC_DIR": str(qfw_install_prefix / "libexec" / "qfw"),
        "QFW_SHARE_DIR": str(qfw_share_dir(qfw_install_prefix)),
        "DEFW_PREFIX": str(defw_install_prefix),
        "DEFW_PATH": str(defw_install_prefix),
        "DEFW_CONFIG_PATH": str(
            defw_install_prefix / "share" / "defw" /
            "config" / "defw_generic.yaml"
        ),
        "QFW_SITE_CONFIG": str(site_config_path),
        "QFW_RUNTIME_CONFIG": str(runtime_config_path),
        "QFW_RUN_ID": run_id,
        "QFW_RUN_TMP_PATH": str(run_root),
        "QFW_LOG_DIR": str(log_dir),
        "QFW_QPM_RESOLVER_SCOPE_ORDER": ",".join(scope_order),
    }
    _persist_caller_environment(environment)
    if profile:
        environment["QFW_RUNTIME_PROFILE"] = profile
    if site_dir:
        site_endpoints = site_dir.get("endpoints") or [site_dir["endpoint"]]
        environment["QFW_SITE_DIRSVC_ENDPOINTS"] = ",".join(site_endpoints)
        environment["QFW_SITE_DIRSVC_NAME"] = site_dir["name"]
        environment["QFW_DIRSVC_CONNECT_TIMEOUT_SECONDS"] = str(
            site_dir["connect_timeout_seconds"])
        if site_dir.get("connection_file"):
            environment["QFW_DIRECTORY_SERVICE_INFO"] = str(
                site_dir["connection_file"])
    if local_endpoint:
        environment["QFW_LOCAL_DIRSVC_ENDPOINT"] = local_endpoint
        environment["QFW_LOCAL_DIRSVC_NAME"] = local_name
        environment["DEFW_PARENT_HOSTNAME"] = local_host
        environment["DEFW_PARENT_PORT"] = str(local_port)
        environment["DEFW_PARENT_NAME"] = local_name
        environment["QFW_DVM_URI_PATH"] = str(
            run_root / "prte_dvm" / "dvm-uri")
    if manifest_path is not None:
        environment["QFW_LOCAL_SERVICE_CONFIG"] = str(manifest_path)
        environment["QFW_SERVICE_SCOPE"] = "allocation-local"
    if dry_run:
        environment["QFW_STARTUP_DRY_RUN"] = "1"

    directory_requirements = directory_readiness_requirements(
        scope_order,
        site_dir,
        {
            "endpoint": local_endpoint,
            "name": local_name,
            "connect_timeout_seconds": int(
                (local_config.get("dirsvc") or {}).get(
                    "connect-timeout-seconds",
                    local_config.get("connect-timeout-seconds", 300),
                )
            ) if local_config else 300,
        } if local_endpoint else {},
    )

    state = {
        "run_id": run_id,
        "run_base_dir": str(run_base),
        "run_dir": str(run_root),
        "log_dir": str(log_dir),
        "state_dir": str(state_dir),
        "site_config": str(site_config_path),
        "runtime_config": str(runtime_config_path),
        "profile": profile,
        "resolver_scope_order": scope_order,
        "directory_requirements": directory_requirements,
        "site_directory": site_dir,
        "local_services": local_config,
        "local_dirsvc": {
            "name": local_name,
            "host": local_host,
            "port": local_port,
            "telnet_port": local_telnet_port,
            "endpoint": local_endpoint,
        },
        "service_manifest": str(manifest_path) if manifest_path else "",
        "environment": environment,
        "service_managers": [],
        "dry_run": bool(dry_run),
        "setup_complete": False,
    }
    write_state(state)
    write_env_file(state)
    write_current_run(run_base, run_id)
    return state


def state_path(state):
    return Path(state["state_dir"]) / "runtime-state.json"


def write_state(state):
    with state_path(state).open("w", encoding="utf-8") as stream:
        json.dump(state, stream, indent=2, sort_keys=True)
        stream.write("\n")


def read_state(run_dir=None):
    if run_dir is None:
        run_dir = current_run_dir()
    path = Path(run_dir) / "state" / "runtime-state.json"
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def write_env_file(state):
    path = Path(state["run_dir"]) / "qfw-runtime-env.sh"
    with path.open("w", encoding="utf-8") as stream:
        for name, value in sorted(state["environment"].items()):
            stream.write(f"export {name}={shlex.quote(str(value))}\n")


def write_current_run(run_base, run_id):
    run_base = Path(run_base)
    run_base.mkdir(parents=True, exist_ok=True)
    with (run_base / "current").open("w", encoding="utf-8") as stream:
        stream.write(f"{run_id}\n")


def current_run_dir():
    if os.environ.get("QFW_RUN_TMP_PATH"):
        return Path(os.environ["QFW_RUN_TMP_PATH"]).expanduser().resolve()
    run_base = qfw_run_base_dir()
    current = run_base / "current"
    if not current.exists():
        raise FileNotFoundError(
            "No QFw runtime state is active; run qfw-setup first")
    run_id = current.read_text(encoding="utf-8").strip()
    if not run_id:
        raise FileNotFoundError(f"empty QFw current run marker: {current}")
    return run_base / run_id


def clear_current_run(state):
    current = Path(state["run_base_dir"]) / "current"
    if not current.exists():
        return
    if current.read_text(encoding="utf-8").strip() == state["run_id"]:
        current.unlink()


def service_by_name(services, service_id):
    for service in services:
        if str(service.get("name", "")) == service_id:
            return dict(service)
    raise ValueError(f"service not found in manifest: {service_id}")


def _free_tcp_port(host):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
