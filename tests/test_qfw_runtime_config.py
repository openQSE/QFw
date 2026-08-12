import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "setup"))

from qfw_runtime import config as qfw_config


def test_site_service_config_resolves_site_owned_paths(tmp_path):
    site_path = tmp_path / "config" / "site.yaml"
    site = {
        "install": {
            "qfw-prefix": str(tmp_path / "qfw"),
            "defw-prefix": str(tmp_path / "defw"),
        },
        "service": {
            "manifest": "site-services.yaml",
            "device-access-config": "<prefix>/etc/device-access.yaml",
        },
    }

    selected = qfw_config.site_service_config(
        site, site_config_path=site_path)

    assert selected == {
        "manifest": site_path.parent / "site-services.yaml",
        "device_access_config": tmp_path / "qfw" / "etc" /
        "device-access.yaml",
    }


def test_site_qpm_config_returns_common_qpm_settings():
    qpm = {
        "completion-queues": {
            "retention": {"completion-ttl-seconds": 42},
        },
    }

    assert qfw_config.site_qpm_config({"qpm": qpm}) == qpm


def test_site_service_config_requires_mapping():
    with pytest.raises(ValueError, match="service must be a mapping"):
        qfw_config.site_service_config({"service": "invalid"})


def test_site_service_config_ignores_removed_aliases():
    selected = qfw_config.site_service_config({
        "service": {
            "service-manifest": "/legacy/services.yaml",
            "service_manifest": "/legacy/services.yaml",
            "device_access_config": "/legacy/devices.yaml",
        },
    })

    assert selected == {}


def test_prepare_run_state_persists_resolver_environment_overrides(
        tmp_path, monkeypatch):
    site_path = tmp_path / "site.yaml"
    runtime_path = tmp_path / "runtime.yaml"
    site = {
        "install": {
            "qfw-prefix": str(tmp_path / "qfw"),
            "defw-prefix": str(tmp_path / "defw"),
        },
        "directory": {
            "site": {
                "name": "yaml-site-dirsvc",
                "endpoint": "yaml-site:8090",
                "connect-timeout-seconds": 11,
            },
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
