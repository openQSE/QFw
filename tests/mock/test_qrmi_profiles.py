import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = str(REPO_ROOT / "services")
if SERVICES not in sys.path:
	sys.path.insert(0, SERVICES)

from svc_lib_qpm.drivers.qrmi_driver import QrmiDriver  # noqa: E402
from svc_lib_qpm.drivers.qrmi_profiles import (  # noqa: E402
	IbmQrmiProfile, IqmQrmiProfile, PasqalQrmiProfile, profile_for,
	profile_for_resource_type)


@pytest.mark.parametrize("descriptor, profile", [
	(None, IqmQrmiProfile),
	({}, IqmQrmiProfile),
	({"provider": None}, IqmQrmiProfile),
	({"provider": "IBM"}, IbmQrmiProfile),
	({"provider": "pasqal"}, PasqalQrmiProfile),
	({"provider": "alicebob"}, IqmQrmiProfile),
])
def test_a_descriptor_picks_its_providers_profile(descriptor, profile):
	assert type(profile_for(descriptor)) is profile


@pytest.mark.parametrize("type_name, profile", [
	("IQMServer", IqmQrmiProfile),
	("IBMQuantumSystem", IbmQrmiProfile),
	("PasqalCloud", PasqalQrmiProfile),
	("AliceBobFelis", type(None)),
])
def test_a_resource_type_picks_the_profile_that_serves_it(type_name, profile):
	assert type(profile_for_resource_type(type_name, {})) is profile


def test_a_pasqal_driver_serves_only_the_pasqal_calls():
	driver = QrmiDriver({"provider": "pasqal"})

	assert driver.implements("get_backend_info")
	assert not driver.implements("get_device_info")
	assert QrmiDriver({"provider": "ibm"}).implements("get_device_info")
