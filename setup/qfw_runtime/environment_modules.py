"""Resolve site environment modules for a QFw service process."""

import json
import os
import shutil
import subprocess
import sys


class EnvironmentModuleError(RuntimeError):
    """Report an environment-module resolution or validation failure."""


def configured_names(service, key):
    value = service.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    names = []
    for item in value:
        name = str(item).strip()
        if not name:
            raise ValueError(f"{key} entries must not be empty")
        names.append(name)
    return names


def load_for_service(service, environment=None):
    environment = dict(environment or os.environ)
    modules = configured_names(service, "environment-modules")
    if modules:
        environment = load_modules(modules, environment)
    validate_executables(
        configured_names(service, "required-executables"), environment)
    return environment


def load_modules(modules, environment):
    modulecmd = shutil.which("modulecmd", path=environment.get("PATH"))
    if not modulecmd:
        raise EnvironmentModuleError(
            "environment modules are required but modulecmd is unavailable")

    command = [modulecmd, "python", "load", *modules]
    result = subprocess.run(
        command,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise EnvironmentModuleError(
            f"unable to load environment modules {', '.join(modules)}: "
            f"{detail or f'modulecmd exited with {result.returncode}'}")

    export_script = (
        result.stdout
        + "\nimport json, os\n"
        + "print(json.dumps(dict(os.environ), sort_keys=True))\n"
    )
    resolved = subprocess.run(
        [sys.executable, "-c", export_script],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if resolved.returncode:
        detail = resolved.stderr.strip()
        raise EnvironmentModuleError(
            "modulecmd produced an invalid environment update"
            + (f": {detail}" if detail else ""))
    try:
        loaded = json.loads(resolved.stdout)
    except json.JSONDecodeError as exc:
        raise EnvironmentModuleError(
            "modulecmd did not produce a valid environment") from exc
    if not isinstance(loaded, dict):
        raise EnvironmentModuleError(
            "modulecmd did not produce an environment mapping")
    return {str(key): str(value) for key, value in loaded.items()}


def validate_executables(executables, environment):
    missing = [
        executable for executable in executables
        if not shutil.which(executable, path=environment.get("PATH"))
    ]
    if missing:
        raise EnvironmentModuleError(
            "required executable(s) unavailable after module loading: "
            + ", ".join(missing))
