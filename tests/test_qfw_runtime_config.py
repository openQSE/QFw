import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))

from qfw_runtime import config as qfw_config


def test_site_service_config_resolves_environment_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("QFW_PREFIX", str(tmp_path / "qfw"))
    monkeypatch.setenv("DEFW_PREFIX", str(tmp_path / "defw"))
    site_path = tmp_path / "config" / "site.yaml"
    site = {
        "install": {
            "qfw-prefix": "${QFW_PREFIX}",
            "defw-prefix": "${DEFW_PREFIX}",
        },
        "service": {
            "manifest": "site-services.yaml",
            "device-access-config": "${QFW_PREFIX}/etc/device-access.yaml",
        },
    }

    assert qfw_config.site_install_prefixes(site) == {
        "qfw_prefix": tmp_path / "qfw",
        "defw_prefix": tmp_path / "defw",
    }
    selected = qfw_config.site_service_config(
        site, site_config_path=site_path)

    assert selected == {
        "manifest": site_path.parent / "site-services.yaml",
        "device_access_config": tmp_path / "qfw" / "etc" /
        "device-access.yaml",
    }


@pytest.mark.parametrize("key,replacement", [
    ("qfw_prefix", "qfw-prefix"),
    ("defw_prefix", "defw-prefix"),
    ("prefix", "qfw-prefix"),
])
def test_site_install_prefixes_rejects_removed_keys(key, replacement):
    with pytest.raises(ValueError, match=replacement):
        qfw_config.site_install_prefixes({"install": {key: "/opt/qfw"}})


@pytest.mark.parametrize("key,replacement", [
    ("listen_port", "listen-port"),
    ("telnet_port", "telnet-port"),
])
def test_service_manifest_rejects_removed_port_keys(
        tmp_path, key, replacement):
    manifest = tmp_path / "services.yaml"
    manifest.write_text(
        f"services:\n  - name: test\n    {key}: 8090\n",
        encoding="utf-8")

    with pytest.raises(ValueError, match=replacement):
        qfw_config.load_service_manifest(manifest)


def test_expand_config_value_requires_braced_environment_reference(
        monkeypatch):
    monkeypatch.setenv("QFW_PREFIX", "/opt/qfw")

    with pytest.raises(ValueError, match="must use braced form"):
        qfw_config.expand_config_value("$QFW_PREFIX/share/qfw")


@pytest.mark.parametrize("placeholder", [
    "<prefix>",
    "<qfw-prefix>",
    "<defw-prefix>",
])
def test_expand_config_value_rejects_removed_placeholders(placeholder):
    with pytest.raises(ValueError, match="unsupported configuration"):
        qfw_config.expand_config_value(placeholder)


def test_expand_config_value_rejects_unset_environment(monkeypatch):
    monkeypatch.delenv("QFW_MISSING_PREFIX", raising=False)

    with pytest.raises(ValueError, match="unset environment variable"):
        qfw_config.expand_config_value("${QFW_MISSING_PREFIX}/share/qfw")


def test_expand_config_value_rejects_empty_environment(monkeypatch):
    monkeypatch.setenv("QFW_EMPTY_PREFIX", "")

    with pytest.raises(ValueError, match="empty environment variable"):
        qfw_config.expand_config_value("${QFW_EMPTY_PREFIX}/share/qfw")


def test_expand_config_value_rejects_invalid_reference():
    with pytest.raises(ValueError, match="invalid environment variable"):
        qfw_config.expand_config_value("${QFW-PREFIX}/share/qfw")


def test_site_directory_uses_published_connection_record(tmp_path):
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "instance-1",
        "name": "site-dirsvc",
        "endpoint": "service-a:18090",
        "ready": True,
    }) + "\n", encoding="utf-8")
    site = {
        "directory-service": {
            "name": "configured-name",
            "listen-port": 18090,
            "connect-timeout-seconds": 17,
            "connection-file": str(connection_file),
        },
    }

    assert qfw_config.site_directory(site) == {
        "name": "site-dirsvc",
        "listen_port": 18090,
        "connect_timeout_seconds": 17,
        "connection_file": str(connection_file),
        "endpoint": "service-a:18090",
        "endpoints": ["service-a:18090"],
        "instance_id": "instance-1",
    }


def test_site_directory_rejects_unready_connection_record(tmp_path):
    connection_file = tmp_path / "directory-service.json"
    connection_file.write_text(json.dumps({
        "schema": "qfw-directory-service-v1",
        "instance_id": "instance-1",
        "name": "site-dirsvc",
        "endpoint": "service-a:18090",
        "ready": False,
    }) + "\n", encoding="utf-8")
    site = {
        "directory-service": {
            "connection-file": str(connection_file),
        },
    }

    with pytest.raises(RuntimeError, match="not ready"):
        qfw_config.site_directory(site)


def test_site_service_config_requires_mapping():
    with pytest.raises(ValueError, match="service must be a mapping"):
        qfw_config.site_service_config({"service": "invalid"})


@pytest.mark.parametrize("key,replacement", [
    ("service-manifest", "manifest"),
    ("service_manifest", "manifest"),
    ("device_access_config", "device-access-config"),
])
def test_site_service_config_rejects_removed_keys(key, replacement):
    with pytest.raises(ValueError, match=replacement):
        qfw_config.site_service_config({"service": {key: "/removed/path"}})


def test_prepare_run_state_persists_resolver_environment_overrides(
        tmp_path, monkeypatch):
    site_path = tmp_path / "site.yaml"
    runtime_path = tmp_path / "runtime.yaml"
    site = {
        "install": {
            "qfw-prefix": str(tmp_path / "qfw"),
            "defw-prefix": str(tmp_path / "defw"),
        },
        "directory-service": {
            "name": "yaml-site-dirsvc",
            "listen-port": 8090,
            "connect-timeout-seconds": 11,
            "connection-file": str(tmp_path / "directory-service.json"),
        },
    }
    runtime = {
        "resolver": {
            "scope-order": ["site"],
        },
    }
    monkeypatch.setenv(
        "QFW_SITE_DIRSVC_ENDPOINTS",
        "override-site-a:9000;override-site-b:9001",
    )
    monkeypatch.setenv("QFW_QPM_RESOLVER_SCOPE_ORDER", "direct,site")

    state = qfw_config.prepare_run_state(
        site_path,
        runtime_path,
        site,
        runtime,
        run_id="override-test",
        run_dir=tmp_path / "run",
        dry_run=True,
    )

    assert state["resolver_scope_order"] == ["direct", "site"]
    assert state["environment"]["QFW_QPM_RESOLVER_SCOPE_ORDER"] == "direct,site"
    assert state["environment"]["QFW_SITE_DIRSVC_ENDPOINTS"] == (
        "override-site-a:9000,override-site-b:9001")
    assert state["site_directory"]["endpoint"] == "override-site-a:9000"
    assert state["site_directory"]["endpoints"] == [
        "override-site-a:9000",
        "override-site-b:9001",
    ]
    assert [
        requirement["endpoint"]
        for requirement in state["directory_requirements"]
    ] == [
        "override-site-a:9000",
        "override-site-b:9001",
    ]


def test_prepare_run_state_persists_local_dvm_uri(tmp_path):
    site_path = tmp_path / "site.yaml"
    runtime_path = tmp_path / "runtime.yaml"
    site = {
        "install": {
            "qfw-prefix": str(tmp_path / "qfw"),
            "defw-prefix": str(tmp_path / "defw"),
        },
    }
    runtime = {
        "resolver": {
            "scope-order": ["local"],
        },
        "local-services": {
            "start-prte": True,
            "start-dirsvc": True,
            "start-qpm": True,
        },
    }

    state = qfw_config.prepare_run_state(
        site_path,
        runtime_path,
        site,
        runtime,
        run_id="local-test",
        run_dir=tmp_path / "run",
        dry_run=True,
    )

    assert state["environment"]["QFW_DVM_URI_PATH"] == str(
        tmp_path / "run" / "prte_dvm" / "dvm-uri")
    assert state["local_dirsvc"]["telnet_port"] == 0


def test_prepare_run_state_selects_one_profile_service(tmp_path):
    site_path = tmp_path / "site.yaml"
    runtime_path = tmp_path / "runtime.yaml"
    site = {
        "install": {
            "qfw-prefix": str(tmp_path / "qfw"),
            "defw-prefix": str(tmp_path / "defw"),
        },
    }
    runtime = {
        "resolver": {
            "scope-order": ["local"],
        },
        "local-services": {
            "start-dirsvc": True,
            "start-qpm": True,
        },
    }

    state = qfw_config.prepare_run_state(
        site_path,
        runtime_path,
        site,
        runtime,
        run_id="selected-service-test",
        run_dir=tmp_path / "run",
        dry_run=True,
        service_id="nwqsim",
    )

    assert state["local_services"]["services"] == ["nwqsim"]


def test_prepare_run_state_rejects_service_without_local_profile(tmp_path):
    with pytest.raises(ValueError, match="requires a runtime profile"):
        qfw_config.prepare_run_state(
            tmp_path / "site.yaml",
            tmp_path / "runtime.yaml",
            {},
            {"resolver": {"scope-order": ["site"]}},
            run_id="invalid-service-test",
            run_dir=tmp_path / "run",
            dry_run=True,
            service_id="nwqsim",
        )


def test_local_directory_rejects_duplicate_listen_and_telnet_ports():
    local = {
        "dirsvc": {
            "port": 8100,
            "telnet-port": 8100,
        },
    }

    with pytest.raises(ValueError, match="listen and telnet ports must differ"):
        qfw_config.allocate_local_telnet_port(local, "127.0.0.1", 8100)


def test_local_directory_allocates_distinct_telnet_port(monkeypatch):
    ports = iter([8100, 8101])
    monkeypatch.setattr(
        qfw_config,
        "_free_tcp_port",
        lambda _host: next(ports),
    )

    port = qfw_config.allocate_local_telnet_port(
        {"dirsvc": {"telnet-port": "auto"}},
        "127.0.0.1",
        8100,
    )

    assert port == 8101


def test_prepare_run_state_persists_user_python_environment(
        tmp_path, monkeypatch):
    site_path = tmp_path / "site.yaml"
    runtime_path = tmp_path / "runtime.yaml"
    site = {
        "install": {
            "qfw-prefix": str(tmp_path / "qfw"),
            "defw-prefix": str(tmp_path / "defw"),
        },
    }
    runtime = {
        "resolver": {
            "scope-order": ["direct"],
        },
    }
    monkeypatch.setenv("PATH", "/shared/venv/bin:/usr/bin")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/shared/qfw/lib")
    monkeypatch.setenv("PYTHONPATH", "/shared/venv/site-packages")
    monkeypatch.setenv("VIRTUAL_ENV", "/shared/venv")
    monkeypatch.setenv("VIRTUAL_ENV_PROMPT", "(qfw)")

    state = qfw_config.prepare_run_state(
        site_path,
        runtime_path,
        site,
        runtime,
        run_id="venv-test",
        run_dir=tmp_path / "run",
        dry_run=True,
    )

    assert state["environment"]["PATH"] == "/shared/venv/bin:/usr/bin"
    assert state["environment"]["LD_LIBRARY_PATH"] == "/shared/qfw/lib"
    assert state["environment"]["PYTHONPATH"] == (
        "/shared/venv/site-packages")
    assert state["environment"]["VIRTUAL_ENV"] == "/shared/venv"
    assert state["environment"]["VIRTUAL_ENV_PROMPT"] == "(qfw)"
