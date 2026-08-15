import subprocess
from pathlib import Path


SCRIPT = (
	Path(__file__).resolve().parents[2]
	/ "examples"
	/ "qfw_iqm_site_services.sh"
)


def test_iqm_site_service_exposes_directory_telnet_port():
	result = subprocess.run(
		["bash", str(SCRIPT), "--help"],
		check=True,
		capture_output=True,
		text=True,
	)

	assert "--dirsvc-telnet-port PORT" in result.stdout


def test_iqm_site_service_forwards_directory_telnet_port():
	script = SCRIPT.read_text(encoding="utf-8")

	assert '--telnet-port "${dirsvc_telnet_port}"' in script
