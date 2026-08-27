import pytest

from setup.qfw_runtime import config
from setup.qfw_runtime import environment_modules


def _executable(path, contents):
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_load_for_service_resolves_module_environment(tmp_path):
    module_bin = tmp_path / "module-bin"
    simulator_bin = tmp_path / "simulator-bin"
    module_bin.mkdir()
    simulator_bin.mkdir()
    _executable(
        module_bin / "modulecmd",
        "#!/bin/sh\n"
        "printf '%s\\n' \"import os\"\n"
        f"printf '%s\\n' \"os.environ['PATH'] = "
        f"'{simulator_bin}:' + os.environ['PATH']\"\n"
        "printf '%s\\n' \"os.environ['LOADEDMODULES'] = 'nwqsim'\"\n",
    )
    _executable(simulator_bin / "circuit_runner.nwqsim", "#!/bin/sh\n")
    base = {
        "PATH": f"{module_bin}:/usr/bin:/bin",
        "MODULEPATH": "/site/modulefiles",
    }

    resolved = environment_modules.load_for_service({
        "environment-modules": ["nwqsim"],
        "required-executables": ["circuit_runner.nwqsim"],
    }, base)

    assert resolved["LOADEDMODULES"] == "nwqsim"
    assert resolved["PATH"].startswith(f"{simulator_bin}:")
    assert "LOADEDMODULES" not in base


def test_load_for_service_reports_module_failure(tmp_path):
    modulecmd = _executable(
        tmp_path / "modulecmd",
        "#!/bin/sh\necho 'module not found' >&2\nexit 1\n",
    )

    with pytest.raises(
            environment_modules.EnvironmentModuleError,
            match="module not found"):
        environment_modules.load_for_service({
            "environment-modules": ["missing"],
        }, {"PATH": str(modulecmd.parent)})


def test_load_for_service_reports_missing_executable():
    with pytest.raises(
            environment_modules.EnvironmentModuleError,
            match="circuit_runner.tnqvm"):
        environment_modules.load_for_service({
            "required-executables": ["circuit_runner.tnqvm"],
        }, {"PATH": "/usr/bin:/bin"})


def test_service_manifest_requires_module_lists(tmp_path):
    manifest = tmp_path / "services.yaml"
    manifest.write_text(
        "services:\n"
        "  - name: nwqsim\n"
        "    environment-modules: nwqsim\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="environment-modules must be a list"):
        config.load_service_manifest(manifest)
