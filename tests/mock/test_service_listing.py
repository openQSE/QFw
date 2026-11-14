import json
import pathlib

import pytest
import yaml

from util import service_listing

LOCAL_SERVICES = (
	pathlib.Path(__file__).resolve().parents[2] /
	"share" / "qfw" / "config" / "services" / "local-services.yaml")


def local_service_entries():
	return yaml.safe_load(LOCAL_SERVICES.read_text())["services"]


@pytest.mark.parametrize(
	"entry", local_service_entries(), ids=lambda entry: entry["name"])
def test_every_local_service_module_names_its_provider(entry):
	assert service_listing.service_record(entry)["provider"]


@pytest.mark.parametrize("module, provider", [
	("svc_nwqsim_qpm", "nwqsim"),
	("svc_tnqvm_qpm", "tnqvm"),
])
def test_a_simulator_lists_the_provider_its_qpm_advertises(module, provider):
	assert service_listing.service_record(
		{"name": provider, "module": module})["provider"] == provider


@pytest.mark.parametrize("aer, accelerator", [
	({"device": "GPU"}, "gpu"),
	({"device": "CPU"}, None),
	({}, None),
])
def test_an_aer_service_lists_the_device_it_runs_on(aer, accelerator):
	record = service_listing.service_record(
		{"name": "aer-gpu", "module": "svc_aer_qpm", "aer": aer})

	assert record == {
		"service": "aer-gpu",
		"provider": "aer",
		"device_id": None,
		"accelerator": accelerator,
	}


def test_a_service_without_an_accelerator_setting_lists_none():
	record = service_listing.service_record({
		"name": "fake-iqm",
		"module": "svc_fake_iqm_qpm",
		"device-id": "fake-iqm-20q",
	})

	assert record == {
		"service": "fake-iqm",
		"provider": "fake-iqm",
		"device_id": "fake-iqm-20q",
		"accelerator": None,
	}


def test_the_listing_prints_one_record_per_entry(capsys):
	entries = [
		{"name": "aer", "module": "svc_aer_qpm", "aer": {"device": "GPU"}},
		{"name": "nwqsim", "module": "svc_nwqsim_qpm"},
	]

	assert service_listing.main([json.dumps(entries)]) == 0
	assert json.loads(capsys.readouterr().out) == [
		{"service": "aer", "provider": "aer", "device_id": None,
		 "accelerator": "gpu"},
		{"service": "nwqsim", "provider": "nwqsim", "device_id": None,
		 "accelerator": None},
	]
