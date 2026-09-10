import json
import os
from pathlib import Path

SCRIPT = (
	Path(__file__).resolve().parents[2]
	/ "examples"
	/ "qfw_iqm_chem_driver.sh"
)


def _write_site_config(tmp_path):
	chem_app_dir = tmp_path / "chem"
	chem_app_dir.mkdir()
	(chem_app_dir / "smoke.py").write_text("", encoding="utf-8")

	credential_db = tmp_path / "qpu_users.json"
	credential_db.write_text(json.dumps({
		"users": {"release-user": {"api_key": "test-only"}},
	}), encoding="utf-8")
	device_access = tmp_path / "device-access.yaml"
	device_access.write_text(
		"qpus:\n"
		"  ornl-iqm-20q:\n"
		"    provider: iqm\n"
		"    provider-device-id: default\n"
		f"    credential-db: {credential_db}\n",
		encoding="utf-8",
	)
	site_config = tmp_path / "site.yaml"
	site_config.write_text(
		"service:\n"
		f"  device-access-config: {device_access}\n",
		encoding="utf-8",
	)
	return chem_app_dir, device_access, site_config


def _script_env(tmp_path):
	env = os.environ.copy()
	env["QFW_SITE_CONFIG"] = str(tmp_path / "installed-default.yaml")
	setup_path = str(SCRIPT.parent.parent / "setup")
	old_pythonpath = env.get("PYTHONPATH")
	env["PYTHONPATH"] = (
		setup_path if not old_pythonpath
		else setup_path + os.pathsep + old_pythonpath)
	return env


def test_service_run_dir_overrides_environment_site_config(
		tmp_path, run_example_script):
	service_run_dir = tmp_path / "services"
	env_dir = service_run_dir / "env"
	env_dir.mkdir(parents=True)
	chem_app_dir, _, site_config = _write_site_config(tmp_path)
	(env_dir / "iqm-site.env").write_text(
		f"export QFW_SITE_CONFIG={site_config}\n",
		encoding="utf-8",
	)

	env = _script_env(tmp_path)
	result = run_example_script(
		[
			"bash",
			str(SCRIPT),
			"--service-run-dir",
			str(service_run_dir),
			"--chem-app-dir",
			str(chem_app_dir),
			"--owner",
			"release-user",
			"--preflight-only",
			"smoke.py",
		],
		env=env,
	)

	assert '"status": "ok"' in result.stdout
	assert str(site_config) not in result.stderr


def test_explicit_site_config_overrides_environment(tmp_path, run_example_script):
	chem_app_dir, device_access, site_config = _write_site_config(tmp_path)

	env = _script_env(tmp_path)
	result = run_example_script(
		[
			"bash",
			str(SCRIPT),
			"--site-config",
			str(site_config),
			"--chem-app-dir",
			str(chem_app_dir),
			"--owner",
			"release-user",
			"--preflight-only",
			"smoke.py",
		],
		env=env,
	)

	assert '"status": "ok"' in result.stdout
	assert str(device_access) in result.stdout
	assert "installed-default.yaml" not in result.stdout
