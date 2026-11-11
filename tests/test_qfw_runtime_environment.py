"""What a QFw run inherits from the process that submitted it.

A workflow manager that imports DEFw and then calls sbatch passes DEFw's
placeholders for its own process into the job, and a submitter inside a QFw
run passes that run's variables (openQSE/QFw#187). The application, the
services and the run discovery must use the job's own run instead.
"""

from pathlib import Path
import json
import sys
import types

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "setup"))
sys.path.insert(0, str(_REPO_ROOT / "services"))
sys.path.insert(0, str(_REPO_ROOT / "DEFw" / "python" / "infra"))

# See test_qfw_runtime_ports.py: CI checks DEFw out but does not build it.
try:
    import cdefw_global  # noqa: F401
except ImportError:
    _cdefw_global = types.ModuleType("cdefw_global")
    _cdefw_global.get_node_name = lambda: "qfw-test"
    sys.modules["cdefw_global"] = _cdefw_global

from qfw_runtime import commands
from qfw_runtime import config as qfw_config
from qfw_runtime import _process_launcher as process_launcher


# What importing DEFw left in a Prefect worker's environment, as recorded in
# openQSE/QFw#187, plus the installation paths and log levels that
# qfw-activate and the job script set.
DEFW_IMPORT_PLACEHOLDERS = {
    "DEFW_AGENT_NAME": "defw_python_58dccf81",
    "DEFW_AGENT_TYPE": "agent",
    "DEFW_DISABLE_DIRSVC": "yes",
    "DEFW_LISTEN_PORT": "0",
    "DEFW_LOG_DIR": "/tmp/defw_python_58dccf81",
    "DEFW_PARENT_ADDR": "0.0.0.0",
    "DEFW_PARENT_HOSTNAME": "None",
    "DEFW_PARENT_NAME": "None",
    "DEFW_PARENT_PORT": "0",
    "DEFW_SHELL_TYPE": "cmdline",
    "DEFW_TELNET_PORT": "0",
}
CALLER_SETTINGS = {
    "DEFW_LOG_LEVEL": "error",
    "DEFW_PY_LOGLEVEL": "critical",
    "DEFW_EXTERNAL_SERVICES_PATH": "/opt/openqse/qfw/lib/qfw/services",
    "DEFW_EXTERNAL_SERVICE_APIS_PATH": "/opt/openqse/qfw/lib/qfw/service-apis",
    "DEFW_LOAD_NO_INIT": "api_qpm_common",
    "QFW_SITE_CONFIG": "/etc/openqse/qfw/site.yaml",
    "QFW_RESERVATIONS": '[["nwqsim","5"]]',
}
# A submitter inside a QFw run with an allocation-local directory.
OTHER_RUN = {
    "QFW_RUN_ID": "submitter-run",
    "QFW_RUN_TMP_PATH": "/tmp/qfw-runs/submitter-run",
    "QFW_LOG_DIR": "/tmp/qfw-runs/submitter-run/logs",
    "QFW_LOCAL_DIRSVC_ENDPOINT": "login-1:41000",
    "QFW_LOCAL_DIRSVC_NAME": "qfw-local-dirsvc",
    "QFW_DIRECTORY_SERVICE_INFO":
        "/tmp/qfw-runs/submitter-run/service-plane/directory/directory.json",
    "QFW_QPM_SERVICE_IDS": "nwqsim-submitter-run",
    "QFW_SERVICE_SCOPE": "allocation-local",
    "QFW_ALLOCATION_MODE": "local",
    "QFW_GROUP_0_NODELIST": "login-1",
    "QFW_GROUP_1_NODELIST": "login-1",
    "QFW_GROUPS": "GROUP_0=login-1:GROUP_1=login-1",
}


def _set_environment(monkeypatch, *mappings):
    for mapping in mappings:
        for name, value in mapping.items():
            monkeypatch.setenv(name, value)


def _write_state(run_dir, state):
    state_dir = run_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {"run_dir": str(run_dir), "state_dir": str(state_dir), **state}
    (state_dir / "runtime-state.json").write_text(
        json.dumps(state), encoding="utf-8")
    return state


def _capture_qfw_srun(monkeypatch, run_dir, *args):
    captured = {}

    def fake_run(argv, env):
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands, "_command_path",
                        lambda name, env=None: Path("/usr/bin/defw-python"))
    monkeypatch.setattr(commands, "_free_tcp_port", lambda host: 45680)
    monkeypatch.setattr(commands, "_resolve_host_address",
                        lambda host: "10.0.0.4")
    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    rc = commands.qfw_srun(["--run-dir", str(run_dir), *args, "app.py"])
    assert rc == 0
    return captured


def _site_only_state(run_dir, slurm_job_id="42"):
    return _write_state(run_dir, {
        "setup_complete": True,
        "run_id": "job-run",
        "slurm_job_id": slurm_job_id,
        "environment": {
            "QFW_RUN_ID": "job-run",
            "QFW_RUN_TMP_PATH": str(run_dir),
            "QFW_SITE_DIRSVC_ENDPOINTS": "slurmctld:8090",
            "QFW_SITE_DIRSVC_NAME": "qfw-site-dirsvc",
            "QFW_QPM_RESOLVER_SCOPE_ORDER": "site",
        },
    })


def test_qfw_srun_ignores_defw_placeholders_from_an_importer(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _site_only_state(run_dir)
    _set_environment(
        monkeypatch, DEFW_IMPORT_PLACEHOLDERS, CALLER_SETTINGS,
        {"SLURM_JOB_ID": "42", "SLURM_JOB_NODELIST": "c1"})

    env = _capture_qfw_srun(monkeypatch, run_dir)["env"]

    assert env["DEFW_PARENT_HOSTNAME"] == "slurmctld"
    assert env["DEFW_PARENT_PORT"] == "8090"
    assert env["DEFW_PARENT_NAME"] == "qfw-site-dirsvc"
    assert env["DEFW_PARENT_ADDR"] == "10.0.0.4"
    assert env["DEFW_DISABLE_DIRSVC"] == "no"
    assert env["DEFW_LISTEN_PORT"] == "45680"
    assert env["DEFW_LOG_DIR"] == str(run_dir / "application" / "logs")
    # DEFw names the application itself instead of reusing the submitter's
    # agent name, and no inherited port reaches it.
    assert "DEFW_AGENT_NAME" not in env
    assert "DEFW_TELNET_PORT" not in env
    for name, value in CALLER_SETTINGS.items():
        assert env[name] == value


def test_qfw_srun_takes_run_variables_from_its_own_run(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _site_only_state(run_dir)
    _set_environment(
        monkeypatch, OTHER_RUN,
        {"SLURM_JOB_ID": "42", "SLURM_JOB_NODELIST": "c1"})

    captured = _capture_qfw_srun(monkeypatch, run_dir)
    env = captured["env"]

    # The submitter's allocation-local directory and allocation would win
    # over this run's site directory and Slurm allocation.
    assert env["DEFW_PARENT_HOSTNAME"] == "slurmctld"
    assert "QFW_LOCAL_DIRSVC_ENDPOINT" not in env
    assert env["QFW_ALLOCATION_MODE"] == "slurm"
    assert env["QFW_GROUP_0_NODELIST"] == "c1"
    assert captured["argv"][0] == "srun"
    assert env["QFW_RUN_ID"] == "job-run"
    assert env["QFW_RUN_TMP_PATH"] == str(run_dir)
    for name in ("QFW_QPM_SERVICE_IDS", "QFW_SERVICE_SCOPE", "QFW_LOG_DIR",
                 "QFW_DIRECTORY_SERVICE_INFO"):
        assert name not in env


def test_qfw_target_queries_on_this_node_with_the_run_environment(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _site_only_state(run_dir)
    _set_environment(
        monkeypatch, DEFW_IMPORT_PLACEHOLDERS,
        {"SLURM_JOB_ID": "42", "SLURM_JOB_NODELIST": "c1"})
    captured = {}

    def fake_run(argv, env):
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        return commands.subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(commands, "_command_path",
                        lambda name, env=None: Path("/usr/bin/defw-python"))
    monkeypatch.setattr(commands, "_free_tcp_port", lambda host: 45680)
    monkeypatch.setattr(commands, "_resolve_host_address",
                        lambda host: "10.0.0.4")
    monkeypatch.setattr(commands.subprocess, "run", fake_run)
    # qfw-activate puts the backends on PYTHONPATH.
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "backends"))

    assert commands.qfw_target([
        "--run-dir", str(run_dir), "--provider", "aer"]) == 0

    argv = captured["argv"]
    assert argv[0] == "/usr/bin/defw-python"
    assert argv[1].endswith("qfw_qiskit/qfw_target_query.py")
    assert Path(argv[1]).is_file()
    assert argv[2:] == ["--provider", "aer"]
    assert captured["env"]["DEFW_PARENT_HOSTNAME"] == "slurmctld"


def test_qfw_target_refuses_an_unready_run(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _write_state(run_dir, {"setup_complete": False})

    assert commands.qfw_target(["--run-dir", str(run_dir)]) == 1


def test_qfw_srun_keeps_the_directory_of_a_job_local_run(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    _write_state(run_dir, {
        "setup_complete": True,
        "slurm_job_id": "42",
        "environment": {
            "QFW_ALLOCATION_MODE": "slurm",
            "QFW_GROUP_0_NODELIST": "c1",
            "QFW_GROUP_1_NODELIST": "c2",
            "QFW_GROUPS": "GROUP_0=c1:GROUP_1=c2",
            "QFW_LOCAL_DIRSVC_ENDPOINT": "c2:41000",
            "QFW_LOCAL_DIRSVC_NAME": "qfw-local-dirsvc",
            "DEFW_PARENT_HOSTNAME": "c2",
            "DEFW_PARENT_PORT": "41000",
            "DEFW_PARENT_NAME": "qfw-local-dirsvc",
            "DEFW_DISABLE_DIRSVC": "no",
        },
    })
    _set_environment(monkeypatch, DEFW_IMPORT_PLACEHOLDERS,
                     {"SLURM_JOB_ID": "42"})

    env = _capture_qfw_srun(monkeypatch, run_dir)["env"]

    assert env["DEFW_PARENT_HOSTNAME"] == "c2"
    assert env["DEFW_PARENT_PORT"] == "41000"
    assert env["DEFW_PARENT_NAME"] == "qfw-local-dirsvc"
    assert env["QFW_LOCAL_DIRSVC_ENDPOINT"] == "c2:41000"


def test_qfw_setup_discards_another_process_and_another_run():
    environ = {
        **DEFW_IMPORT_PLACEHOLDERS, **CALLER_SETTINGS, **OTHER_RUN,
        "QFW_RUN_BASE_DIR": "/workspace/qfw-runs/job-42",
        "QFW_QPM_RESOLVER_SCOPE_ORDER": "site",
    }

    qfw_config.discard_inherited_environment(environ)

    assert environ == {
        **CALLER_SETTINGS,
        "QFW_RUN_BASE_DIR": "/workspace/qfw-runs/job-42",
        "QFW_QPM_RESOLVER_SCOPE_ORDER": "site",
    }


def test_qfw_setup_keeps_a_caller_supplied_run_id():
    environ = {
        "QFW_RUN_ID": "chosen-by-caller",
        "QFW_DIRECTORY_SERVICE_INFO": "/etc/openqse/qfw/directory.json",
    }

    qfw_config.discard_inherited_environment(environ)

    assert environ == {
        "QFW_RUN_ID": "chosen-by-caller",
        "QFW_DIRECTORY_SERVICE_INFO": "/etc/openqse/qfw/directory.json",
    }


def test_qfw_setup_names_what_it_ignores(tmp_path, monkeypatch, capsys):
    _set_environment(monkeypatch, DEFW_IMPORT_PLACEHOLDERS, OTHER_RUN)
    monkeypatch.setattr(
        qfw_config, "resolve_site_config",
        lambda explicit: (_ for _ in ()).throw(ValueError("stop here")))

    try:
        commands.qfw_setup([])
    except ValueError:
        pass

    stderr = capsys.readouterr().err
    assert "qfw-setup: ignoring inherited" in stderr
    assert "DEFW_PARENT_HOSTNAME" in stderr
    assert "QFW_RUN_ID" in stderr
    for name in [*DEFW_IMPORT_PLACEHOLDERS, *OTHER_RUN]:
        assert name not in commands.os.environ


def test_prepare_run_state_records_the_slurm_job(tmp_path, monkeypatch):
    monkeypatch.setenv("SLURM_JOB_ID", "42")
    site = {
        "install": {
            "qfw-prefix": str(tmp_path / "qfw"),
            "defw-prefix": str(tmp_path / "defw"),
        },
        "directory-service": {
            "name": "qfw-site-dirsvc",
            "endpoint": "slurmctld:8090",
        },
    }

    state = qfw_config.prepare_run_state(
        tmp_path / "site.yaml", tmp_path / "runtime.yaml", site, {},
        run_id="job-run", run_dir=tmp_path / "run", dry_run=True)

    assert state["slurm_job_id"] == "42"


def _two_runs(tmp_path, monkeypatch, other_job, this_job):
    base = tmp_path / "runs"
    other = _write_state(base / "other", {"slurm_job_id": other_job})
    this = _write_state(base / "this", {"slurm_job_id": this_job})
    (base / "current").write_text("this\n", encoding="utf-8")
    monkeypatch.setenv("QFW_RUN_BASE_DIR", str(base))
    monkeypatch.setenv("QFW_RUN_TMP_PATH", other["run_dir"])
    return (Path(other["run_dir"]).resolve(),
            Path(this["run_dir"]).resolve())


def test_run_discovery_ignores_a_run_from_another_slurm_job(
        tmp_path, monkeypatch, capsys):
    other, this = _two_runs(tmp_path, monkeypatch, "7", "42")
    monkeypatch.setenv("SLURM_JOB_ID", "42")

    assert qfw_config.current_run_dir().resolve() == this
    stderr = capsys.readouterr().err
    assert "ignoring QFW_RUN_TMP_PATH" in stderr
    assert "Slurm job 7" in stderr


def test_run_discovery_ignores_a_run_from_outside_the_job(
        tmp_path, monkeypatch):
    other, this = _two_runs(tmp_path, monkeypatch, "", "42")
    monkeypatch.setenv("SLURM_JOB_ID", "42")

    assert qfw_config.current_run_dir().resolve() == this


def test_run_discovery_ignores_a_run_this_node_cannot_read(
        tmp_path, monkeypatch):
    other, this = _two_runs(tmp_path, monkeypatch, "7", "42")
    monkeypatch.setenv("SLURM_JOB_ID", "42")
    monkeypatch.setenv("QFW_RUN_TMP_PATH", str(tmp_path / "elsewhere"))

    assert qfw_config.current_run_dir().resolve() == this


def test_run_discovery_uses_the_run_of_this_job(tmp_path, monkeypatch):
    other, this = _two_runs(tmp_path, monkeypatch, "42", "42")
    monkeypatch.setenv("SLURM_JOB_ID", "42")

    assert qfw_config.current_run_dir().resolve() == other


def test_run_discovery_outside_slurm_uses_a_run_from_outside_slurm(
        tmp_path, monkeypatch):
    other, this = _two_runs(tmp_path, monkeypatch, "", "")
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)

    assert qfw_config.current_run_dir().resolve() == other


def test_service_launcher_ignores_defw_placeholders(tmp_path, monkeypatch):
    manifest_path = tmp_path / "site-services.yaml"
    manifest_path.write_text(
        "services:\n"
        "  - name: nwqsim\n"
        "    module: svc_nwqsim_qpm\n"
        "    credential-mode: no-secret\n",
        encoding="utf-8",
    )
    site_path = tmp_path / "site.yaml"
    site_path.write_text(
        "directory-service:\n"
        "  endpoint: slurmctld:8090\n"
        "service:\n"
        f"  manifest: {manifest_path}\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_start(name, env, *args):
        captured["env"] = dict(env)
        return 0

    _set_environment(monkeypatch, DEFW_IMPORT_PLACEHOLDERS, CALLER_SETTINGS,
                     {"QFW_SERVICE_SCOPE": "site"})
    monkeypatch.setattr(
        process_launcher, "_start_defw_owned_process", fake_start)

    rc = process_launcher.start_qpm([
        "--service-id", "nwqsim-runtime",
        "--manifest-service-name", "nwqsim",
        "--site-config", str(site_path),
        "--run-dir", str(tmp_path / "run"),
        "--operation-mode", "long-running",
    ])

    assert rc == 0
    env = captured["env"]
    assert env["DEFW_AGENT_NAME"] == "nwqsim-runtime"
    assert env["DEFW_PARENT_HOSTNAME"] == "slurmctld"
    assert env["DEFW_DISABLE_DIRSVC"] == "no"
    assert "DEFW_PARENT_ADDR" not in env
    assert env["DEFW_LOG_DIR"] != DEFW_IMPORT_PLACEHOLDERS["DEFW_LOG_DIR"]
    assert env["DEFW_EXTERNAL_SERVICES_PATH"] == (
        CALLER_SETTINGS["DEFW_EXTERNAL_SERVICES_PATH"])
    # The launcher's caller sets the run variables a service needs.
    assert env["QFW_SERVICE_SCOPE"] == "site"
