import json
import os
import subprocess
from pathlib import Path

SCRIPT = (
	Path(__file__).resolve().parents[2]
	/ "examples"
	/ "qfw_iqm_chem_driver.sh"
)


def test_explicit_site_config_overrides_environment(tmp_path):
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
	env = os.environ.copy()
	env["QFW_SITE_CONFIG"] = str(tmp_path / "installed-default.yaml")
	env["PYTHONPATH"] = str(SCRIPT.parent.parent / "setup")
	result = subprocess.run(
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
		check=True,
		capture_output=True,
		env=env,
		text=True,
	)

	assert '"status": "ok"' in result.stdout
	assert str(device_access) in result.stdout
	assert "installed-default.yaml" not in result.stdout
