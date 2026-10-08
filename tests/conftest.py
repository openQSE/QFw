"""
The suite runs with telemetry off, whatever the environment says.

Inside the reference cluster every container carries QFW_TELEMETRY and the
collector's address, so a test that builds a backend or starts a QPM would
configure telemetry from them and report its fixture failures to the live
collector as if they were jobs. Tests that want telemetry adopt a recording
provider through qfw_telemetry.use_providers, which ignores the profile in
the environment.
"""
import os

import pytest

_TELEMETRY_VARIABLES = (
	"QFW_TELEMETRY", "QFW_TELEMETRY_ENDPOINT", "QFW_TELEMETRY_SAMPLE",
	"QFW_TELEMETRY_TRANSPORT", "QFW_TELEMETRY_DIR")


@pytest.fixture(autouse=True, scope="session")
def _telemetry_off_for_the_suite():
	saved = {name: os.environ.pop(name) for name in _TELEMETRY_VARIABLES
		if name in os.environ}
	os.environ["QFW_TELEMETRY"] = "off"
	try:
		yield
	finally:
		os.environ.pop("QFW_TELEMETRY", None)
		os.environ.update(saved)
