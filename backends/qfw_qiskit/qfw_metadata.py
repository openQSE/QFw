QFW_METADATA_NAMESPACE = "qfw"
QFW_METADATA_QUBIT_MAPPING = "qubit_mapping"


def normalize_qubit_mapping(mapping):
	if mapping is None:
		return None
	if isinstance(mapping, dict):
		normalized = {
			str(logical): str(physical)
			for logical, physical in mapping.items()
			if physical is not None
		}
	elif isinstance(mapping, (list, tuple)):
		normalized = {
			str(logical): str(physical)
			for logical, physical in enumerate(mapping)
			if physical is not None
		}
	else:
		raise TypeError("qubit mapping must be a dict, list, or tuple")
	return normalized or None


def set_qubit_mapping(circuit, mapping):
	normalized = normalize_qubit_mapping(mapping)
	metadata = dict(getattr(circuit, "metadata", None) or {})
	qfw_metadata = dict(metadata.get(QFW_METADATA_NAMESPACE) or {})
	qfw_metadata[QFW_METADATA_QUBIT_MAPPING] = normalized
	metadata[QFW_METADATA_NAMESPACE] = qfw_metadata
	circuit.metadata = metadata
	return circuit


def get_qubit_mapping(circuit):
	metadata = getattr(circuit, "metadata", None) or {}
	qfw_metadata = metadata.get(QFW_METADATA_NAMESPACE) or {}
	return normalize_qubit_mapping(
		qfw_metadata.get(QFW_METADATA_QUBIT_MAPPING))
