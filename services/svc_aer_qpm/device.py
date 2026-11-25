import json
import os


def ibm_device_records(fake_backend_name_or_records_file):
	spec = fake_backend_name_or_records_file
	if spec.endswith(".json") or os.path.sep in spec:
		with open(spec, encoding="utf-8") as stream:
			records = json.load(stream)
		return records["configuration"], records.get("properties")
	from qiskit_ibm_runtime import fake_provider
	fake_class = getattr(fake_provider, spec, None)
	if fake_class is None:
		raise ValueError(f"{spec!r} is not a qiskit-ibm-runtime fake_provider "
				 "class or a calibration file")
	fake = fake_class()
	return (
		_load_json(fake.dirname, fake.conf_filename),
		_load_json(fake.dirname, fake.props_filename))


def _load_json(directory, filename):
	if not filename:
		return None
	with open(os.path.join(directory, filename), encoding="utf-8") as stream:
		return json.load(stream)


def device_backend(fake_backend_name_or_records_file):
	from qiskit_ibm_runtime import fake_provider
	spec = fake_backend_name_or_records_file
	if hasattr(fake_provider, spec):
		return getattr(fake_provider, spec)()
	return recorded_backend(*ibm_device_records(spec))


def recorded_backend(configuration, properties):
	from qiskit.providers import BackendV2, Options
	from qiskit_ibm_runtime.models import (
		BackendConfiguration, BackendProperties)
	from qiskit_ibm_runtime.utils.backend_converter import convert_to_target

	class RecordedBackend(BackendV2):
		@property
		def target(self):
			return target

		@property
		def max_circuits(self):
			return None

		@classmethod
		def _default_options(cls):
			return Options()

		def run(self, run_input, **options):
			raise NotImplementedError

	target = convert_to_target(
		BackendConfiguration.from_dict(configuration),
		BackendProperties.from_dict(properties))
	return RecordedBackend(name=configuration["backend_name"])
