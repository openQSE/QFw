import logging
from dataclasses import dataclass, field

from qiskit.circuit import Gate, Measure, Parameter, Reset
from qiskit.circuit.library import (
	CCXGate,
	CHGate,
	CSGate,
	CSXGate,
	CSdgGate,
	CSwapGate,
	CUGate,
	CXGate,
	CYGate,
	CZGate,
	CPhaseGate,
	CRXGate,
	CRYGate,
	CRZGate,
	HGate,
	IGate,
	PhaseGate,
	RXGate,
	RXXGate,
	RYGate,
	RYYGate,
	RZGate,
	RZZGate,
	SGate,
	SXGate,
	SXdgGate,
	SdgGate,
	SwapGate,
	TGate,
	TdgGate,
	U1Gate,
	U2Gate,
	U3Gate,
	UGate,
	XGate,
	YGate,
	ZGate,
)
from qiskit.transpiler import InstructionProperties, Target


QFW_NUM_QUBITS = 400


def qfw_basis_gates():
	return [
		"x", "y", "z", "h", "s", "sdg", "t", "tdg", "rx", "ry",
		"rz", "sx", "sxdg", "p", "u", "cx", "cy", "cz", "ch", "cs",
		"csdg", "crx", "cry", "crz", "csx", "cp", "cu", "id", "swap",
		"measure", "reset", "u1", "u2", "u3", "ccx", "cswap", "rxx",
		"ryy", "rzz",
	]


def qfw_instructions():
	# One instance of every instruction in qfw_basis_gates(), the vocabulary
	# of the generic target and the first place a device operation's name is
	# looked up.
	return [
		IGate(),
		XGate(),
		YGate(),
		ZGate(),
		HGate(),
		SGate(),
		SdgGate(),
		TGate(),
		TdgGate(),
		SXGate(),
		SXdgGate(),
		RXGate(0),
		RYGate(0),
		RZGate(0),
		PhaseGate(0),
		UGate(0, 0, 0),
		U1Gate(0),
		U2Gate(0, 0),
		U3Gate(0, 0, 0),
		CXGate(),
		CYGate(),
		CZGate(),
		CHGate(),
		CSGate(),
		CSdgGate(),
		CSXGate(),
		CPhaseGate(0),
		CUGate(0, 0, 0, 0),
		CRXGate(0),
		CRYGate(0),
		CRZGate(0),
		SwapGate(),
		RXXGate(0),
		RYYGate(0),
		RZZGate(0),
		CCXGate(),
		CSwapGate(),
		Measure(),
		Reset(),
	]


def build_qfw_target(num_qubits=QFW_NUM_QUBITS):
	# The generic target: every gate QFw serializes, on any qubit, with no
	# connectivity. It stands in when a QPM describes no device.
	target = Target(
		description="Quantum Framework (QFw) Target",
		num_qubits=num_qubits,
	)

	for instruction in qfw_instructions():
		target.add_instruction(instruction)

	return target


# ---------------------------------------------------------------------------
# Device targets, built from what the QPM's telemetry API reports.
#
# A QPM publishes its device through the telemetry binding (api_qpm_telemetry):
# get_backend_info() says whether it has metadata at all, and for the shim
# carries the provider's static architecture; get_device_info(),
# get_coupling_graph() and get_calibration_snapshot() are the qhw-data records
# (qhw-device-v1, qhw-coupling-v1, qhw-calibration-v1) or, for a QPM that does
# not normalize, plain dicts with the same meaning. The functions below turn
# any of those shapes into one Qiskit Target: the device's qubits, its native
# operations on the loci they are calibrated for, and the measured error and
# duration of each, when the snapshot has them.
# ---------------------------------------------------------------------------

# Provider names for operations, mapped onto the names Qiskit's library uses.
# IQM calls its phased X rotation prx (Qiskit: r) and its reset reset_wait;
# Braket spells several standard gates its own way.
INSTRUCTION_ALIASES = {
	"prx": "r",
	"reset_wait": "reset",
	"cnot": "cx",
	"ccnot": "ccx",
	"toffoli": "ccx",
	"i": "id",
	"phaseshift": "p",
	"cphaseshift": "cp",
	"si": "sdg",
	"ti": "tdg",
	"v": "sx",
	"vi": "sxdg",
	"xx": "rxx",
	"yy": "ryy",
	"zz": "rzz",
}

# Provider operations that are not instructions a Qiskit circuit carries: IQM's
# classically controlled prx, its 1-2 transition pulse and its measurement
# fidelity probe, and the barrier, which the transpiler keeps without a target
# entry.
SKIPPED_OPERATIONS = {"cc_prx", "prx_12", "measure_fidelity", "barrier"}

# Two-qubit operations that act the same either way round. A device lists each
# of their loci once; the target gets both orders.
SYMMETRIC_OPERATIONS = {
	"cz", "ccz", "swap", "iswap", "rxx", "ryy", "rzz", "cp", "cs", "csdg",
}

# Duration units a calibration record may carry, to seconds. A unit that is not
# here leaves the duration out rather than guess at it.
DURATION_UNITS = {
	"s": 1.0,
	"ms": 1e-3,
	"us": 1e-6,
	"µs": 1e-6,
	"ns": 1e-9,
	"ps": 1e-12,
}

# The IQM quality-metric families whose fidelity is a gate's fidelity on its
# locus: randomized benchmarking (prx), interleaved RB (cz) and single-shot
# readout (measure). The others (qndness, clifford) describe something else.
IQM_FIDELITY_FAMILIES = ("rb", "irb", "ssro")

TELEMETRY_RECORD_CALLS = (
	"get_device_info", "get_coupling_graph", "get_calibration_snapshot")


@dataclass
class DeviceOperation:
	name: str
	arity: int
	# Qubit-index tuples the operation is offered on; None means anywhere.
	loci: list = None
	parameters: list = field(default_factory=list)


@dataclass
class DeviceDescription:
	provider: str
	device_id: str
	num_qubits: int
	# Position is the qubit index the target uses; the label is the device's.
	qubit_ids: list = field(default_factory=list)
	edges: list = field(default_factory=list)
	directed: bool = False
	operations: list = field(default_factory=list)
	# (operation name, locus labels) -> {"error": ..., "duration": ...}
	metrics: dict = field(default_factory=dict)
	# qubit label -> {"t1": ..., "t2": ...}, seconds
	qubit_metrics: dict = field(default_factory=dict)
	calibration_set_id: str = None

	def index_of(self, label):
		key = str(label)
		positions = {name: index for index, name in enumerate(self.qubit_ids)}
		if key in positions:
			return positions[key]
		if isinstance(label, bool):
			return None
		if isinstance(label, int) and 0 <= label < self.num_qubits:
			return label
		if key.isdigit() and int(key) < self.num_qubits:
			return int(key)
		return None

	def locus(self, labels):
		# The index tuple for a locus of device labels; None when one of them
		# is not a qubit of this device (IQM lists couplers and probe lines
		# among the loci of reset_wait).
		indices = []
		for label in labels:
			index = self.index_of(label)
			if index is None:
				return None
			indices.append(index)
		return tuple(indices)

	def labels(self, locus):
		return tuple(self.qubit_ids[index] for index in locus)


def metadata_supported(backend_info):
	return _truthy((backend_info or {}).get("metadata_supported"))


def is_ibm_configuration(static_architecture):
	# The shim's IBM record puts the raw BackendConfiguration dict under
	# static_architecture; IQM's static architecture has neither key.
	return (
		isinstance(static_architecture, dict) and
		"basis_gates" in static_architecture and
		"n_qubits" in static_architecture)


def target_from_telemetry(telemetry, lib=None, backend_info=None):
	"""Build the Target the QPM's telemetry describes.

	Returns None when the QPM reports no device metadata (the simulators).
	get_backend_info() decides (a caller that already holds its record
	passes it as backend_info), and for an IBM device also carries the raw
	configuration and properties, so nothing more is fetched for it. For
	every other device the three record calls follow; each one is optional,
	since a QPM that cannot answer one still has a device to describe from
	the rest.
	"""
	info = backend_info
	if info is None:
		info = telemetry.get_backend_info(lib=lib)
	info = info or {}
	if not metadata_supported(info):
		return None
	if is_ibm_configuration(info.get("static_architecture")):
		return build_device_target(info)
	records = []
	for call in TELEMETRY_RECORD_CALLS:
		try:
			records.append(getattr(telemetry, call)(lib=lib))
		except Exception as exc:
			logging.info("QFw target: %s unavailable, building without it: %s",
				     call, exc)
			records.append(None)
	return build_device_target(info, *records)


def build_device_target(backend_info, device_info=None, coupling_graph=None,
			calibration=None):
	"""The Target for the device these telemetry records describe.

	Returns None when backend_info reports no device metadata. An IBM record
	(raw BackendConfiguration under static_architecture, raw
	BackendProperties under backend_properties) goes through
	qiskit_ibm_runtime's converter; any other shape is read as a device
	description: the qubits, the coupling edges, the native operations and
	their loci, and the calibration snapshot's errors and durations.
	"""
	info = backend_info or {}
	if not metadata_supported(info):
		return None
	static = info.get("static_architecture")
	if is_ibm_configuration(static):
		return ibm_target(static, info.get("backend_properties"))
	return target_from_description(
		describe_device(info, device_info, coupling_graph, calibration))


# --- IBM: the raw BackendConfiguration and BackendProperties ---------------

def ibm_target(configuration, properties=None):
	converter = _ibm_converter()
	if converter is None:
		logging.info("QFw target: qiskit_ibm_runtime is not installed; the "
			     "IBM target carries the configuration without its "
			     "calibration")
		return target_from_description(
			describe_ibm_configuration(configuration))
	convert_to_target, BackendConfiguration, BackendProperties = converter
	backend_configuration = BackendConfiguration.from_dict(configuration)
	backend_properties = None
	if properties:
		try:
			backend_properties = BackendProperties.from_dict(properties)
		except Exception as exc:
			logging.warning("QFw target: the IBM backend properties could "
					"not be read (%s); the target carries no "
					"calibration", exc)
	target = convert_to_target(backend_configuration, backend_properties)
	if not target.description:
		try:
			target.description = (
				f"QFw ibm {configuration.get('backend_name') or 'ibm'} target")
		except AttributeError:
			logging.debug("QFw target: the IBM target's description is read-only")
	return target


def _ibm_converter():
	# qiskit_ibm_runtime is the IBM shim driver's dependency, not the
	# client's, so a client without it still gets a target.
	try:
		from qiskit_ibm_runtime.utils.backend_converter import convert_to_target
		from qiskit_ibm_runtime.models import BackendProperties, BackendConfiguration
	except ImportError:
		return None
	return convert_to_target, BackendConfiguration, BackendProperties


def describe_ibm_configuration(configuration):
	# What a BackendConfiguration dict says on its own: the qubit count, the
	# directed coupling map and, per basis gate, the loci it is offered on.
	num_qubits = int(configuration.get("n_qubits") or 0)
	description = DeviceDescription(
		provider="ibm",
		device_id=str(configuration.get("backend_name") or "ibm"),
		num_qubits=num_qubits,
		qubit_ids=[str(index) for index in range(num_qubits)],
		directed=True,
	)
	description.edges = _unique([
		description.locus(edge)
		for edge in configuration.get("coupling_map") or []
		if description.locus(edge) is not None and len(edge) == 2])
	gates = {
		gate.get("name"): gate
		for gate in configuration.get("gates") or []
		if isinstance(gate, dict)}
	for name in configuration.get("basis_gates") or []:
		gate = gates.get(name) or {}
		loci = [
			description.locus(locus)
			for locus in gate.get("coupling_map") or []
			if description.locus(locus) is not None] or None
		description.operations.append(DeviceOperation(
			str(name),
			len(loci[0]) if loci else None,
			loci,
			[str(parameter) for parameter in gate.get("parameters") or []]))
	everywhere = [(index,) for index in range(num_qubits)]
	for name in ("measure", "reset"):
		description.operations.append(DeviceOperation(name, 1, everywhere))
	return description


# --- qhw records and plain device dicts -------------------------------------

def describe_device(backend_info, device_info=None, coupling_graph=None,
		    calibration=None):
	"""Read the telemetry records into one DeviceDescription.

	The qubits come from the qhw device record (embedded in backend_info by
	the shim, or returned by get_device_info), else from the coupling
	record's nodes, else from a plain qubit count. The edges and operations
	come from the coupling record (qhw-coupling-v1, or a plain "couplings"
	list), the metrics from the calibration snapshot's extensions.
	"""
	info = backend_info or {}
	device = device_info if isinstance(device_info, dict) else {}
	coupling = coupling_graph if isinstance(coupling_graph, dict) else {}
	snapshot = calibration if isinstance(calibration, dict) else {}
	qhw_device = info.get("qhw_device")
	if not isinstance(qhw_device, dict):
		qhw_device = device if device.get("schema") == "qhw-device-v1" else {}
	provider = str(
		info.get("backend") or qhw_device.get("provider") or
		device.get("provider") or coupling.get("provider") or "qpm")
	device_id = str(
		(qhw_device.get("device") or {}).get("id") or
		info.get("target_id") or device.get("target_id") or
		device.get("device_id") or (coupling.get("device") or {}).get("id") or
		provider)
	qubit_ids = _qubit_ids(info, qhw_device, device, coupling)
	description = DeviceDescription(
		provider=provider,
		device_id=device_id,
		num_qubits=len(qubit_ids),
		qubit_ids=qubit_ids,
		calibration_set_id=(
			info.get("calibration_set_id") or
			(snapshot.get("calibration") or {}).get("calibration_set_id")),
	)
	description.edges, description.directed = _edges(coupling, description)
	description.operations = _operations(coupling, description)
	description.metrics, description.qubit_metrics = calibration_metrics(
		snapshot)
	return description


def _qubit_ids(info, qhw_device, device, coupling):
	qubits = qhw_device.get("qubits")
	if isinstance(qubits, list) and qubits:
		labelled = []
		for position, qubit in enumerate(qubits):
			if isinstance(qubit, dict):
				label, index = qubit.get("id"), qubit.get("index")
			else:
				label, index = qubit, None
			labelled.append((position if index is None else int(index),
					 str(label)))
		return [label for _index, label in sorted(labelled)]
	nodes = (coupling.get("coupling") or {}).get("nodes")
	if isinstance(nodes, list) and nodes:
		return [str(node) for node in nodes]
	for count in (
			device.get("num_qubits"),
			(device.get("device") or {}).get("num_qubits"),
			info.get("num_qubits"),
			len(info.get("active_qubits") or [])):
		try:
			count = int(count)
		except (TypeError, ValueError):
			continue
		if count > 0:
			return [str(index) for index in range(count)]
	return []


def _edges(coupling, description):
	graph = coupling.get("coupling")
	if isinstance(graph, dict):
		raw, directed = graph.get("edges") or [], graph.get("directed", False)
	else:
		raw = (coupling.get("couplings") or coupling.get("edges") or
		       coupling.get("coupling_map") or [])
		directed = coupling.get("directed", False)
	edges = []
	for edge in raw:
		locus = description.locus(edge) if isinstance(edge, (list, tuple)) else None
		if locus is not None and len(locus) == 2:
			edges.append(locus)
	return _unique(edges), _truthy(directed)


def _operations(coupling, description):
	operations = []
	for entry in coupling.get("operations") or []:
		if not isinstance(entry, dict) or not entry.get("name"):
			continue
		loci = None
		if entry.get("supported_loci"):
			loci = []
			for locus in entry["supported_loci"]:
				if not locus:
					# A global locus: the operation takes any qubits.
					loci = None
					break
				indices = description.locus(locus)
				if indices is not None:
					loci.append(indices)
		arity = entry.get("arity")
		if arity is None:
			arity = len(loci[0]) if loci else 1
		operations.append(DeviceOperation(
			str(entry["name"]), int(arity), loci,
			[str(parameter) for parameter in entry.get("parameters") or []]))
	return operations


def calibration_metrics(calibration):
	"""Per-locus gate metrics and per-qubit coherence from a calibration record.

	Returns (gate_metrics, qubit_metrics): {(operation, locus labels):
	{"error", "duration"}} and {qubit label: {"t1", "t2"}}, durations in
	seconds. The qhw-calibration-v1 core carries only identity and counts;
	the measured values live in the normalizer's extension, so this reads
	the two extensions the shim produces (qdmi.fomac.v1 and iqm.v1) and
	ignores any other namespace, as the schema asks of generic consumers.
	"""
	gates, qubits = {}, {}
	extensions = (calibration or {}).get("extensions") or {}
	fomac = extensions.get("qdmi.fomac.v1")
	if isinstance(fomac, dict):
		_fomac_metrics(fomac, gates, qubits)
	iqm = extensions.get("iqm.v1")
	if isinstance(iqm, dict):
		_iqm_metrics(iqm, gates, qubits)
	return gates, qubits


def _fomac_metrics(fomac, gates, qubits):
	# qdmi_normalize: gate_metrics [{operation, locus, fidelity?, duration?}]
	# and qubit_metrics [{qubit, t1?, t2?}], times in duration_unit.
	scale = _duration_scale(fomac.get("duration_unit"))
	for entry in fomac.get("gate_metrics") or []:
		if not isinstance(entry, dict) or not entry.get("operation"):
			continue
		locus = tuple(str(label) for label in entry.get("locus") or ())
		metric = gates.setdefault((str(entry["operation"]), locus), {})
		if entry.get("fidelity") is not None:
			metric["error"] = _error_from_fidelity(entry["fidelity"])
		if entry.get("duration") is not None and scale is not None:
			metric["duration"] = float(entry["duration"]) * scale
	for entry in fomac.get("qubit_metrics") or []:
		if not isinstance(entry, dict) or entry.get("qubit") is None:
			continue
		coherence = qubits.setdefault(str(entry["qubit"]), {})
		for key in ("t1", "t2"):
			if entry.get(key) is not None and scale is not None:
				coherence[key] = float(entry[key]) * scale


def _iqm_metrics(iqm, gates, qubits):
	# qhw_iqm keeps IQM's observation sets whole. A quality metric is
	# "metrics.<family>.<gate>.<implementation>.<locus>.fidelity[:par=...]"
	# or "characterization.model.<qubit>.t1_time"; a calibration-set entry
	# "gates.<gate>.<implementation>.<locus>.duration" carries the unit.
	# A two-qubit locus is "QB1__QB2".
	for observation in _observations(iqm.get("quality_metric_set")):
		field_name, value, _unit = observation
		parts = field_name.split(":", 1)[0].split(".")
		if (len(parts) == 6 and parts[0] == "metrics" and
				parts[5] == "fidelity" and
				parts[1] in IQM_FIDELITY_FAMILIES):
			gates.setdefault((parts[2], _iqm_locus(parts[4])), {})["error"] = \
				_error_from_fidelity(value)
		elif (len(parts) == 4 and parts[0] == "characterization" and
				parts[3] in ("t1_time", "t2_time")):
			seconds = _duration_seconds(value, observation[2])
			if seconds is not None:
				qubits.setdefault(parts[2], {})[parts[3][:2]] = seconds
	for field_name, value, unit in _observations(iqm.get("calibration_set")):
		parts = field_name.split(".")
		if len(parts) == 5 and parts[0] == "gates" and parts[4] == "duration":
			seconds = _duration_seconds(value, unit)
			if seconds is not None:
				gates.setdefault((parts[1], _iqm_locus(parts[3])), {})["duration"] = \
					seconds


def _observations(observation_set):
	for observation in (observation_set or {}).get("observations") or []:
		if not isinstance(observation, dict) or observation.get("invalid"):
			continue
		field_name, value = observation.get("dut_field"), observation.get("value")
		if not isinstance(field_name, str) or not _is_number(value):
			continue
		yield field_name, value, observation.get("unit")


def _iqm_locus(text):
	return tuple(text.split("__"))


def _error_from_fidelity(fidelity):
	return min(max(1.0 - float(fidelity), 0.0), 1.0)


def _duration_scale(unit):
	# IQM reports some durations with an empty unit; they are seconds.
	return DURATION_UNITS.get(str(unit or "s").strip().lower())


def _duration_seconds(value, unit):
	scale = _duration_scale(unit)
	if scale is None:
		return None
	return float(value) * scale


# --- the Target itself --------------------------------------------------------

def target_from_description(description):
	target = Target(
		description=f"QFw {description.provider} {description.device_id} target",
		num_qubits=description.num_qubits,
		**_qubit_properties(description))
	catalog = instruction_catalog()
	operations = description.operations or generic_operations(description, catalog)
	added = set()
	for operation in operations:
		if operation.name in SKIPPED_OPERATIONS or operation.loci == []:
			continue
		instruction = _instruction_for(operation, catalog)
		if instruction.name in added:
			logging.debug("QFw target: %s already added, skipping %s",
				      instruction.name, operation.name)
			continue
		target.add_instruction(
			instruction, _instruction_properties(description, operation))
		added.add(instruction.name)
	return target


def instruction_catalog():
	# Instruction instances by name: QFw's own set first, then everything
	# Qiskit's library names (r for IQM's prx, ecr, iswap, delay, ...) on a
	# Qiskit that has the mapping.
	catalog = {instruction.name: instruction for instruction in qfw_instructions()}
	try:
		from qiskit.circuit.library import get_standard_gate_name_mapping
	except ImportError:
		return catalog
	for name, instruction in get_standard_gate_name_mapping().items():
		catalog.setdefault(name, instruction)
	return catalog


def generic_operations(description, catalog):
	# A device that names no operations gets QFw's generic set on its
	# qubits: one-qubit gates on every qubit, two-qubit gates on the coupling
	# edges, and three-qubit gates only where there is no coupling to route
	# over (the transpiler decomposes them otherwise).
	everywhere = [(index,) for index in range(description.num_qubits)] or None
	pairs = _coupled_pairs(description)
	operations = []
	for name in qfw_basis_gates():
		arity = catalog[name].num_qubits
		if arity == 1:
			loci = everywhere
		elif arity == 2:
			loci = pairs
		elif pairs is not None:
			continue
		else:
			loci = None
		operations.append(DeviceOperation(name, arity, loci))
	return operations


def _coupled_pairs(description):
	if not description.edges:
		return None
	pairs = list(description.edges)
	if not description.directed:
		pairs.extend(tuple(reversed(edge)) for edge in description.edges)
	return _unique(pairs)


def _instruction_for(operation, catalog):
	name = INSTRUCTION_ALIASES.get(operation.name, operation.name)
	instruction = catalog.get(name)
	if instruction is not None:
		return instruction
	# A device operation Qiskit has no gate for: an opaque gate of that name
	# keeps it in the target's vocabulary, with the device's parameter names.
	arity = operation.arity
	if arity is None:
		arity = len(operation.loci[0]) if operation.loci else 1
	return Gate(name, arity,
		    [Parameter(parameter) for parameter in operation.parameters])


def _instruction_properties(description, operation):
	if operation.loci is None:
		return None
	name = INSTRUCTION_ALIASES.get(operation.name, operation.name)
	symmetric = (
		operation.arity == 2 and name in SYMMETRIC_OPERATIONS and
		not description.directed)
	properties = {}
	for locus in operation.loci:
		measured = _locus_properties(
			description, operation.name, locus, symmetric)
		properties[locus] = measured
		if symmetric:
			properties.setdefault(tuple(reversed(locus)), measured)
	return properties


def _locus_properties(description, name, locus, symmetric=False):
	labels = description.labels(locus)
	metric = dict(description.metrics.get((name, labels)) or {})
	if symmetric:
		# A symmetric operation's metrics may name the locus either way
		# round (IQM's fidelity and duration observations do), so the
		# reversed locus fills in what the listed one lacks.
		reversed_metric = description.metrics.get(
			(name, tuple(reversed(labels)))) or {}
		for key, value in reversed_metric.items():
			metric.setdefault(key, value)
	if not metric:
		return None
	return InstructionProperties(
		duration=metric.get("duration"), error=metric.get("error"))


def _qubit_properties(description):
	if not description.qubit_metrics:
		return {}
	try:
		from qiskit.providers import QubitProperties
	except ImportError:
		return {}
	properties = []
	for label in description.qubit_ids:
		coherence = description.qubit_metrics.get(label) or {}
		properties.append(QubitProperties(
			t1=coherence.get("t1"), t2=coherence.get("t2")))
	return {"qubit_properties": properties}


def _unique(items):
	seen = set()
	unique = []
	for item in items:
		if item not in seen:
			seen.add(item)
			unique.append(item)
	return unique


def _is_number(value):
	return isinstance(value, (int, float)) and not isinstance(value, bool)


def _truthy(value):
	if isinstance(value, bool):
		return value
	if value is None:
		return False
	if isinstance(value, (int, float)):
		return value != 0
	return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}
