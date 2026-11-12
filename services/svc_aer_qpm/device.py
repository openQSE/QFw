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


def fake_backend_from_ibm_records(configuration, properties):
	from qiskit_ibm_runtime.fake_provider.fake_backend import FakeBackendV2
	from qiskit_ibm_runtime.utils.backend_decoder import (
		decode_backend_configuration, properties_from_server_data)

	class CalibratedBackend(FakeBackendV2):
		def _get_conf_dict_from_json(self):
			decoded = json.loads(json.dumps(configuration))
			decode_backend_configuration(decoded)
			return decoded

		def _set_props_dict_from_json(self):
			decoded = json.loads(json.dumps(properties))
			properties_from_server_data(decoded)
			self._props_dict = decoded

	return CalibratedBackend()
