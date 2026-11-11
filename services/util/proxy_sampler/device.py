import json


def load_emulated_device(class_name):
	"""The qiskit-ibm-runtime fake backend named ``class_name``, such as FakeFez."""

	from qiskit_ibm_runtime import fake_provider

	factory = getattr(fake_provider, class_name, None) if class_name.startswith("Fake") else None
	if factory is None:
		raise ValueError(f"qiskit_ibm_runtime.fake_provider has no device {class_name!r}")
	return factory()


def ibm_backend_info(backend):
	"""The device's raw IBM BackendConfiguration under ``static_architecture`` and BackendProperties under
	``backend_properties``, the telemetry record that ``qfw_target.build_device_target`` turns into a Target."""

	def jsonable(record):
		return json.loads(json.dumps(record.to_dict(), default=str))

	return {
		"metadata_supported": True,
		"static_architecture": jsonable(backend.configuration()),
		"backend_properties": jsonable(backend.properties()),
	}
