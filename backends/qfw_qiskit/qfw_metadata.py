from util.isa import make_marker

QFW_METADATA_NAMESPACE = "qfw"
QFW_METADATA_QUBIT_MAPPING = "qubit_mapping"
QFW_METADATA_ISA = "isa"


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


def mark_isa(circuit, provider, device_id, calibration=None, qubit_labels=None,
		compiler=None):
	"""Mark a circuit as compiled ahead of time for one device, so a QPM runs
	it as it stands or refuses it. `qubit_labels` names each circuit index,
	which an IQM device needs. See util.isa."""
	marker = make_marker(
		provider, device_id, calibration=calibration,
		qubit_labels=qubit_labels, compiler=compiler)
	metadata = dict(getattr(circuit, "metadata", None) or {})
	qfw_metadata = dict(metadata.get(QFW_METADATA_NAMESPACE) or {})
	qfw_metadata[QFW_METADATA_ISA] = marker
	metadata[QFW_METADATA_NAMESPACE] = qfw_metadata
	circuit.metadata = metadata
	return circuit


def get_isa_marker(circuit):
	metadata = getattr(circuit, "metadata", None) or {}
	qfw_metadata = metadata.get(QFW_METADATA_NAMESPACE) or {}
	return qfw_metadata.get(QFW_METADATA_ISA)


def isa_metadata(circuit):
	marker = get_isa_marker(circuit)
	if marker is None:
		return {}
	return {QFW_METADATA_NAMESPACE: {QFW_METADATA_ISA: marker}}
