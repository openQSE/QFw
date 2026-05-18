from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from defw_exception import DEFwExecutionError
from util.device_access import (
	QPU_DEVICE_ENV, resolve_device_access, resolve_qpu_user)
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID
import inspect
import logging
import math
import os
import re
import threading
import time

REQUIRED_ENV = ("QFW_QC_URL", "QFW_API_KEY")
DEFAULT_REQUEST_TIMEOUT = 30.0
DEFAULT_JOB_TIMEOUT = 300.0


def to_jsonable(value):
	if value is None or isinstance(value, (str, int, float, bool)):
		return value
	if isinstance(value, UUID):
		return str(value)
	if isinstance(value, dict):
		return {str(k): to_jsonable(v) for k, v in value.items()}
	if isinstance(value, (list, tuple, set, frozenset)):
		return [to_jsonable(v) for v in value]
	if is_dataclass(value):
		return to_jsonable(asdict(value))
	if hasattr(value, "model_dump"):
		return to_jsonable(value.model_dump(mode="json"))
	if hasattr(value, "dict"):
		return to_jsonable(value.dict())
	return str(value)


def sanitize_url(url):
	parsed = urlsplit(url)
	if parsed.username or parsed.password:
		netloc = parsed.hostname or ""
		if parsed.port:
			netloc = f"{netloc}:{parsed.port}"
		parsed = parsed._replace(netloc=netloc)
	return urlunsplit(parsed)


def load_iqm_client_module():
	try:
		from iqm.iqm_client import IQMClient
	except Exception as exc:
		raise DEFwExecutionError(
			"failed to import iqm-client. Install iqm-client before "
			f"starting the IQM QPM service. Import error: {exc}") from exc

	return IQMClient


def load_iqm_pulse_module():
	try:
		from iqm.pulse import Circuit, CircuitOperation
	except Exception as exc:
		raise DEFwExecutionError(
			f"failed to import iqm.pulse circuit objects: {exc}") from exc
	return Circuit, CircuitOperation


def normalize_qhw_iqm(kind, raw_payload, device_id=None):
	try:
		from qhw_iqm import normalize_calibration, normalize_coupling
		from qhw_iqm import normalize_device, normalize_result
	except Exception as exc:
		raise DEFwExecutionError(
			"failed to import qhw-iqm. Install qhw-iqm and qhw-data "
			f"before starting the IQM QPM service. Import error: {exc}") from exc

	if kind == "device":
		return normalize_device(raw_payload, device_id=device_id)
	if kind == "coupling":
		return normalize_coupling(raw_payload, device_id=device_id)
	if kind == "calibration":
		return normalize_calibration(raw_payload, device_id=device_id)
	if kind == "result":
		return normalize_result(raw_payload, device_id=device_id)
	raise DEFwExecutionError(f"unsupported qhw-iqm normalization kind {kind!r}")


def method_accepts(method, name):
	try:
		signature = inspect.signature(method)
	except (TypeError, ValueError):
		return True
	return name in signature.parameters


def call_iqm_method(method, timeout, *args, **kwargs):
	call_kwargs = dict(kwargs)
	if method_accepts(method, "timeout_secs"):
		call_kwargs["timeout_secs"] = timeout
	return method(*args, **call_kwargs)


def parse_calibration_set_id(value):
	if not value:
		return None
	if isinstance(value, UUID):
		return value
	try:
		return UUID(str(value))
	except ValueError as exc:
		raise DEFwExecutionError(
			f"invalid IQM calibration set id {value!r}: {exc}") from exc


def first_event(timeline, status):
	for event in timeline:
		if event.get("status") == status:
			return event
	return None


def parse_timestamp(value):
	if not value:
		return None
	parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
	if parsed.tzinfo is None:
		parsed = parsed.replace(tzinfo=timezone.utc)
	return parsed.astimezone(timezone.utc)


def seconds_between(start, end):
	if start is None or end is None:
		return None
	return (end - start).total_seconds()


def event_time(timeline, status):
	event = first_event(timeline, status)
	if not event:
		return None
	return parse_timestamp(event.get("timestamp"))


def build_timing_summary(record):
	job_data = record.get("job", {}).get("data") or {}
	timeline = job_data.get("timeline") or []
	client_timing = record.get("timing") or {}

	created = event_time(timeline, "created")
	received = event_time(timeline, "received")
	validation_started = event_time(timeline, "validation_started")
	validation_ended = event_time(timeline, "validation_ended")
	compilation_started = event_time(timeline, "compilation_started")
	compilation_ended = event_time(timeline, "compilation_ended")
	execution_started = event_time(timeline, "execution_started")
	execution_ended = event_time(timeline, "execution_ended")
	post_started = event_time(timeline, "post_processing_started")
	post_ended = event_time(timeline, "post_processing_ended")
	ready = event_time(timeline, "ready")
	completed = event_time(timeline, "completed")

	return {
		"schema": "iqm-timing-summary-v1",
		"job_id": record.get("job", {}).get("id"),
		"job_status": record.get("job", {}).get("status"),
		"client_wall_seconds": {
			"submit": client_timing.get("submit_seconds"),
			"wait": client_timing.get("wait_seconds"),
			"result_fetch": client_timing.get("result_fetch_seconds"),
			"total": client_timing.get("total_wall_seconds"),
		},
		"durations_seconds": {
			"server_total_created_to_completed": seconds_between(
				created, completed),
			"created_to_station_received": seconds_between(created, received),
			"queue_wait_received_to_validation_started": seconds_between(
				received, validation_started),
			"validation": seconds_between(
				validation_started, validation_ended),
			"compilation": seconds_between(
				compilation_started, compilation_ended),
			"execution": seconds_between(execution_started, execution_ended),
			"post_processing": seconds_between(post_started, post_ended),
			"ready_to_completed": seconds_between(ready, completed),
			"pre_execution_created_to_execution_started": seconds_between(
				created, execution_started),
		},
		"timeline_events": to_jsonable(timeline),
	}


def fetch_iqm_section(name, func):
	try:
		return {
			"name": name,
			"ok": True,
			"data": to_jsonable(func()),
			"error": None,
		}
	except Exception as exc:
		return {
			"name": name,
			"ok": False,
			"data": {},
			"error": str(exc),
		}


def summarize_observation_set(data):
	if not isinstance(data, dict):
		data = {}
	observations = data.get("observations", {})
	if not isinstance(observations, dict):
		observations = {}
	return {
		"calibration_set_id": (
			data.get("calibration_set_id")
			or data.get("id")
			or data.get("observation_set_id")),
		"observation_count": len(observations),
		"observation_names": sorted(str(name) for name in observations.keys()),
	}


def get_env_float(name, default):
	value = os.environ.get(name)
	if not value:
		return default
	try:
		return float(value)
	except ValueError as exc:
		raise DEFwExecutionError(
			f"{name} must be a floating point number: {value!r}") from exc


def get_required_env():
	missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
	if missing:
		raise DEFwExecutionError(
			"missing required IQM environment variable(s): "
			f"{', '.join(missing)}")
	return {
		"url": os.environ["QFW_QC_URL"].strip(),
		"api_key": os.environ["QFW_API_KEY"].strip(),
		"quantum_computer": os.environ.get("QFW_IQM_QUANTUM_COMPUTER"),
	}


def load_iqm_service_config():
	if all(os.environ.get(name) for name in REQUIRED_ENV):
		config = get_required_env()
		config["device_id"] = os.environ.get(QPU_DEVICE_ENV, "env")
		config["user"] = resolve_qpu_user()
		return config

	config = resolve_device_access(provider="iqm")
	if not config.get("quantum_computer"):
		config["quantum_computer"] = os.environ.get(
			"QFW_IQM_QUANTUM_COMPUTER")
	return config


def create_iqm_client(client_type, config):
	kwargs = {"token": config["api_key"]}
	if config.get("quantum_computer") and method_accepts(
			client_type, "quantum_computer"):
		kwargs["quantum_computer"] = config["quantum_computer"]
	return client_type(config["url"], **kwargs)


def submit_run_request(client, run_request, use_timeslot):
	submit = client.submit_run_request
	if method_accepts(submit, "use_timeslot"):
		return submit(run_request, use_timeslot=use_timeslot)
	if use_timeslot:
		raise DEFwExecutionError(
			"this iqm-client version does not support use_timeslot")
	return submit(run_request)


def normalize_status(status):
	if hasattr(status, "value"):
		return str(status.value)
	return str(status)


def active_qubits(dynamic_architecture):
	data = to_jsonable(dynamic_architecture)
	qubits = data.get("qubits") or []
	if not qubits:
		raise DEFwExecutionError(
			"IQM dynamic architecture did not report active qubits")
	return [str(qubit) for qubit in qubits]


def logical_to_physical_qubits(qasm_circuit, dynamic_architecture, mapping):
	physical = active_qubits(dynamic_architecture)
	num_qubits = getattr(qasm_circuit, "num_qubits", 0)
	if mapping:
		if isinstance(mapping, dict):
			return {
				index: str(mapping.get(index, mapping.get(str(index))))
				for index in range(num_qubits)
			}
		return {index: str(value) for index, value in enumerate(mapping)}

	if num_qubits > len(physical):
		raise DEFwExecutionError(
			f"circuit requires {num_qubits} qubits but IQM reports "
			f"{len(physical)} active qubits")
	return {index: physical[index] for index in range(num_qubits)}


def eval_angle(value):
	allowed = {"pi": math.pi}
	try:
		return float(eval(value, {"__builtins__": {}}, allowed))
	except Exception as exc:
		raise DEFwExecutionError(
			f"unsupported angle expression in OpenQASM: {value!r}") from exc


def split_qasm_statements(qasm):
	statements = []
	for line in qasm.splitlines():
		line = line.split("//", 1)[0].strip()
		if not line:
			continue
		for part in line.split(";"):
			part = part.strip()
			if part:
				statements.append(part)
	return statements


def load_qiskit_circuit(qasm):
	try:
		from qiskit import QuantumCircuit
		try:
			from qiskit import qasm2
			return qasm2.loads(qasm)
		except Exception:
			return QuantumCircuit.from_qasm_str(qasm)
	except Exception as exc:
		raise DEFwExecutionError(
			f"failed to parse OpenQASM through qiskit: {exc}") from exc


def serialize_qiskit_to_iqm(qasm, dynamic_architecture, mapping):
	Circuit, _ = load_iqm_pulse_module()
	try:
		from iqm.qiskit_iqm import qiskit_to_iqm
	except Exception as exc:
		raise DEFwExecutionError(
			"iqm.qiskit_iqm is required to serialize qiskit circuits "
			f"for IQM execution: {exc}") from exc

	qiskit_circuit = load_qiskit_circuit(qasm)
	index_to_name = logical_to_physical_qubits(
		qiskit_circuit, dynamic_architecture, mapping)
	try:
		instructions = qiskit_to_iqm.serialize_instructions(
			qiskit_circuit, index_to_name)
	except Exception as exc:
		raise DEFwExecutionError(
			"IQM could not serialize the circuit. Transpile the circuit "
			f"to IQM-supported native operations first. Error: {exc}") from exc

	return Circuit(
		name=qiskit_circuit.name or "qfw_iqm_circuit",
		instructions=tuple(instructions),
		metadata={"logical_to_physical": index_to_name},
	)


def parse_ref(ref, qregs):
	ref = ref.strip()
	match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\[(\d+)\]$", ref)
	if match:
		reg, index = match.group(1), int(match.group(2))
		if reg not in qregs or index >= qregs[reg]:
			raise DEFwExecutionError(f"unknown OpenQASM qubit reference {ref}")
		return [(reg, index)]
	if ref in qregs:
		return [(ref, index) for index in range(qregs[ref])]
	raise DEFwExecutionError(f"unknown OpenQASM qubit reference {ref}")


def build_manual_iqm_circuit(qasm, dynamic_architecture, mapping):
	Circuit, CircuitOperation = load_iqm_pulse_module()
	statements = split_qasm_statements(qasm)
	qregs = {}
	cregs = {}
	operations = []

	for statement in statements:
		if statement.startswith("OPENQASM") or statement.startswith("include"):
			continue
		match = re.match(r"^qreg\s+(\w+)\[(\d+)\]$", statement)
		if match:
			qregs[match.group(1)] = int(match.group(2))
			continue
		match = re.match(r"^creg\s+(\w+)\[(\d+)\]$", statement)
		if match:
			cregs[match.group(1)] = int(match.group(2))
			continue

	if not qregs:
		raise DEFwExecutionError("OpenQASM input does not define a qreg")

	total_qubits = sum(qregs.values())
	physical = active_qubits(dynamic_architecture)
	if total_qubits > len(physical):
		raise DEFwExecutionError(
			f"circuit requires {total_qubits} qubits but IQM reports "
			f"{len(physical)} active qubits")

	ordered = []
	for reg, size in qregs.items():
		for index in range(size):
			ordered.append((reg, index))
	if mapping:
		if isinstance(mapping, dict):
			qubit_map = {
				key: str(mapping.get(f"{key[0]}[{key[1]}]",
							 mapping.get(key[1], mapping.get(str(key[1])))))
				for key in ordered
			}
		else:
			qubit_map = {key: str(mapping[index])
				     for index, key in enumerate(ordered)}
	else:
		qubit_map = {key: physical[index] for index, key in enumerate(ordered)}

	for statement in statements:
		if statement.startswith(("OPENQASM", "include", "qreg", "creg")):
			continue
		if statement.startswith("barrier"):
			refs = statement[len("barrier"):].strip()
			locus = []
			for ref in refs.split(","):
				locus.extend(qubit_map[key] for key in parse_ref(ref, qregs))
			operations.append(CircuitOperation(
				name="barrier", locus=tuple(locus), args={}))
			continue
		if statement.startswith("measure"):
			match = re.match(r"^measure\s+(.+)\s+->\s+(.+)$", statement)
			if not match:
				raise DEFwExecutionError(
					f"unsupported OpenQASM measurement: {statement}")
			qrefs = parse_ref(match.group(1), qregs)
			cref = match.group(2).strip()
			key = "m"
			match_cref = re.match(r"^(\w+)\[(\d+)\]$", cref)
			if match_cref:
				key = f"{match_cref.group(1)}{match_cref.group(2)}"
			elif cref not in cregs:
				raise DEFwExecutionError(
					f"unknown OpenQASM classical reference {cref}")
			operations.append(CircuitOperation(
				name="measure",
				locus=tuple(qubit_map[key] for key in qrefs),
				args={"key": key},
			))
			continue
		match = re.match(r"^(\w+)(?:\(([^)]*)\))?\s+(.+)$", statement)
		if not match:
			raise DEFwExecutionError(
				f"unsupported OpenQASM statement: {statement}")
		gate, params, refs = match.group(1), match.group(2), match.group(3)
		refs = [ref.strip() for ref in refs.split(",")]
		locus = tuple(qubit_map[key] for ref in refs for key in parse_ref(ref, qregs))
		if gate == "x":
			operations.append(CircuitOperation(
				name="prx", locus=locus, args={"angle": math.pi,
							       "phase": 0.0}))
		elif gate == "rx" and params is not None:
			operations.append(CircuitOperation(
				name="prx", locus=locus, args={"angle": eval_angle(params),
							       "phase": 0.0}))
		elif gate == "ry" and params is not None:
			operations.append(CircuitOperation(
				name="prx", locus=locus, args={"angle": eval_angle(params),
							       "phase": math.pi / 2}))
		elif gate == "cz":
			operations.append(CircuitOperation(
				name="cz", locus=locus, args={}))
		else:
			raise DEFwExecutionError(
				"IQM service can only manually translate native "
				f"OpenQASM gates x, rx, ry, cz, barrier, measure. "
				f"Unsupported statement: {statement}")

	return Circuit(
		name="qfw_iqm_circuit",
		instructions=tuple(operations),
		metadata={"logical_to_physical": to_jsonable(qubit_map)},
	)


def build_iqm_circuit(qasm, dynamic_architecture, mapping):
	try:
		return serialize_qiskit_to_iqm(qasm, dynamic_architecture, mapping)
	except DEFwExecutionError as exc:
		logging.debug(f"falling back to manual IQM QASM translation: {exc}")
		return build_manual_iqm_circuit(qasm, dynamic_architecture, mapping)


def normalize_counts(measurement_counts):
	data = to_jsonable(measurement_counts)
	if isinstance(data, list) and data:
		first = data[0]
	elif isinstance(data, dict):
		first = data
	else:
		return {}
	if isinstance(first, dict) and "counts" in first:
		return first.get("counts") or {}
	return first if isinstance(first, dict) else {}


def get_dynamic_qubits(data):
	qubits = data.get("qubits") or []
	if isinstance(qubits, dict):
		return list(qubits.keys())
	return qubits


def get_dynamic_couplers(data):
	couplers = data.get("couplers") or []
	if isinstance(couplers, dict):
		return list(couplers.values())
	return couplers


def normalize_locus(value):
	if isinstance(value, str):
		return [part.strip() for part in value.split(",") if part.strip()]
	if isinstance(value, (list, tuple)):
		return [str(part) for part in value]
	return []


def normalize_edge(locus):
	if len(locus) != 2:
		return None
	a, b = locus
	if a == b:
		return None
	return tuple(sorted((a, b)))


def sorted_edges(edges):
	return [list(edge) for edge in sorted(edges)]


def collect_static_component_edges(static_arch):
	edges = set()
	for item in static_arch.get("connectivity", []):
		edge = normalize_edge(normalize_locus(item))
		if edge:
			edges.add(edge)
	return edges


def collect_gate_loci(dynamic_arch):
	gate_loci = {}
	gates = dynamic_arch.get("gates", {})
	if not isinstance(gates, dict):
		return gate_loci

	for gate_name, gate_info in gates.items():
		loci = set()
		if isinstance(gate_info, dict):
			implementations = gate_info.get("implementations", {})
			if isinstance(implementations, dict):
				for implementation in implementations.values():
					if not isinstance(implementation, dict):
						continue
					for locus in implementation.get("loci", []):
						normalized = tuple(normalize_locus(locus))
						if normalized:
							loci.add(normalized)
		gate_loci[str(gate_name)] = [
			list(locus) for locus in sorted(loci)
		]
	return gate_loci


def build_coupling_graph(static_arch, dynamic_arch):
	qubits = sorted(str(q) for q in dynamic_arch.get("qubits")
			or static_arch.get("qubits", []))
	resonators = sorted(str(r) for r in dynamic_arch.get(
		"computational_resonators",
	) or static_arch.get("computational_resonators", []))
	qubit_set = set(qubits)
	component_edges = collect_static_component_edges(static_arch)
	gate_loci = collect_gate_loci(dynamic_arch)

	qubit_edges = set()
	gate_edges = {}
	for gate_name, loci in gate_loci.items():
		edges = set()
		for locus in loci:
			edge = normalize_edge(locus)
			if edge and edge[0] in qubit_set and edge[1] in qubit_set:
				edges.add(edge)
				qubit_edges.add(edge)
		if edges:
			gate_edges[gate_name] = sorted_edges(edges)

	if not qubit_edges:
		for edge in component_edges:
			if edge[0] in qubit_set and edge[1] in qubit_set:
				qubit_edges.add(edge)

	return {
		"qubits": qubits,
		"computational_resonators": resonators,
		"component_edges": sorted_edges(component_edges),
		"qubit_edges": sorted_edges(qubit_edges),
		"couplers": sorted_edges(qubit_edges),
		"gate_loci": gate_loci,
		"gate_edges": gate_edges,
		"source_priority": [
			"dynamic_architecture.gates.*.implementations.*.loci",
			"static_architecture.connectivity",
		],
	}


class IQMServiceClient:
	def __init__(self):
		self._client = None
		self._client_lock = threading.Lock()
		self._request_timeout = get_env_float(
			"QFW_IQM_REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)
		self._job_timeout = get_env_float(
			"QFW_IQM_JOB_TIMEOUT", DEFAULT_JOB_TIMEOUT)
		self._last_metadata = {}
		self._last_timing = {}
		self._latest_cid = None
		self._config = None

	def client(self):
		with self._client_lock:
			if self._client is not None:
				return self._client
			config = load_iqm_service_config()
			client_type = load_iqm_client_module()
			self._client = create_iqm_client(client_type, config)
			self._config = config
			logging.debug(
				"created IQM client for "
				f"{sanitize_url(config['url'])} as {config['user']}")
			return self._client

	def device_id(self):
		if self._config is None:
			self.client()
		if self._config:
			return self._config.get("device_id")
		return os.environ.get(QPU_DEVICE_ENV)

	def normalize_qhw(self, kind, raw_payload):
		return normalize_qhw_iqm(kind, raw_payload, device_id=self.device_id())

	def get_static_architecture(self):
		return call_iqm_method(
			self.client().get_static_quantum_architecture,
			self._request_timeout)

	def get_dynamic_architecture(self, calibration_set_id=None):
		calibration_set_id = parse_calibration_set_id(calibration_set_id)
		return call_iqm_method(
			self.client().get_dynamic_quantum_architecture,
			self._request_timeout,
			calibration_set_id)

	def get_backend_info(self):
		static = to_jsonable(self.get_static_architecture())
		dynamic = to_jsonable(self.get_dynamic_architecture())
		raw_payload = {
			"static_architecture": static,
			"dynamic_architecture": dynamic,
		}
		qhw_device = self.normalize_qhw("device", raw_payload)
		return {
			"backend": "iqm",
			"metadata_supported": True,
			"static_architecture": static,
			"active_qubits": get_dynamic_qubits(dynamic),
			"calibration_set_id": dynamic.get("calibration_set_id"),
			"qhw_device": qhw_device,
			"_raw_iqm": raw_payload,
		}

	def get_device_info(self):
		static = to_jsonable(self.get_static_architecture())
		dynamic = to_jsonable(self.get_dynamic_architecture())
		raw_payload = {
			"static_architecture": static,
			"dynamic_architecture": dynamic,
		}
		return self.normalize_qhw("device", raw_payload)

	def get_dynamic_backend_info(self, calibration_set_id=None):
		dynamic = to_jsonable(
			self.get_dynamic_architecture(calibration_set_id))
		return {
			"backend": "iqm",
			"metadata_supported": True,
			"dynamic_architecture": dynamic,
		}

	def get_calibration_snapshot(self, calibration_set_id=None):
		requested_calibration_set_id = parse_calibration_set_id(
			calibration_set_id)
		dynamic = to_jsonable(
			self.get_dynamic_architecture(requested_calibration_set_id))
		calibration = fetch_iqm_section(
			"calibration_set",
			lambda: call_iqm_method(
				self.client().get_calibration_set,
				self._request_timeout,
				requested_calibration_set_id))
		quality = fetch_iqm_section(
			"quality_metric_set",
			lambda: call_iqm_method(
				self.client().get_quality_metric_set,
				self._request_timeout,
				requested_calibration_set_id))
		errors = {
			result["name"]: result["error"]
			for result in (calibration, quality)
			if not result["ok"]
		}
		raw_payload = {
			"dynamic_architecture": dynamic,
			"calibration_set": calibration["data"],
			"quality_metric_set": quality["data"],
			"errors": errors,
		}
		return self.normalize_qhw("calibration", raw_payload)

	def get_coupling_graph(self, calibration_set_id=None):
		static = to_jsonable(self.get_static_architecture())
		dynamic = to_jsonable(
			self.get_dynamic_architecture(calibration_set_id))
		raw_payload = {
			"static_architecture": static,
			"dynamic_architecture": dynamic,
		}
		return self.normalize_qhw("coupling", raw_payload)

	def get_last_job_timing(self, cid=None):
		if cid is None:
			cid = self._latest_cid
		return self._last_timing.get(cid, {
			"backend": "iqm",
			"cid": cid,
			"metadata_supported": True,
			"timing_available": False,
		})

	def get_last_job_metadata(self, cid=None):
		if cid is None:
			cid = self._latest_cid
		return self._last_metadata.get(cid, {
			"backend": "iqm",
			"cid": cid,
			"metadata_supported": True,
			"metadata_available": False,
		})

	def run_circuit(self, circ):
		info = circ.info
		cid = circ.get_cid()
		calibration_set_id = parse_calibration_set_id(
			info.get("calibration_set_id")
			or info.get("iqm_calibration_set_id"))
		use_timeslot = bool(info.get("use_timeslot", False))
		shots = int(info.get("num_shots", info.get("shots", 1)))
		timeout = float(info.get("timeout", self._job_timeout))
		mapping = info.get("iqm_qubit_mapping") or info.get("qubit_mapping")

		timing = {}
		start = time.monotonic()
		dynamic = self.get_dynamic_architecture(calibration_set_id)
		iqm_circuit = build_iqm_circuit(info["qasm"], dynamic, mapping)
		run_request = self.client().create_run_request(
			[iqm_circuit],
			calibration_set_id=calibration_set_id,
			shots=shots)

		submit_started = time.monotonic()
		job = submit_run_request(self.client(), run_request, use_timeslot)
		if not hasattr(job, "wait_for_completion"):
			raise DEFwExecutionError(
				"iqm-client returned only a job id from submit_run_request. "
				"QFw IQM execution requires CircuitJob polling support.")
		timing["submit_seconds"] = time.monotonic() - submit_started

		wait_started = time.monotonic()
		status = normalize_status(job.wait_for_completion(timeout_secs=timeout))
		timing["wait_seconds"] = time.monotonic() - wait_started
		job_data = to_jsonable(job.data)

		if status != "completed":
			raise DEFwExecutionError(
				f"IQM job {job.job_id} completed with status {status}")

		result_started = time.monotonic()
		measurement_counts = self.client().get_job_measurement_counts(
			job.job_id)
		timing["result_fetch_seconds"] = time.monotonic() - result_started
		timing["total_wall_seconds"] = time.monotonic() - start

		counts_data = to_jsonable(measurement_counts)
		counts = normalize_counts(measurement_counts)
		raw_payload = {
			"circuits": to_jsonable([iqm_circuit]),
			"run_request": to_jsonable(run_request),
			"job": job_data,
			"measurement_counts": counts_data,
		}
		record = {
			"cid": cid,
			"input": {
				"shots": shots,
				"calibration_set_id": str(calibration_set_id)
				if calibration_set_id else None,
				"use_timeslot": use_timeslot,
			},
			"job": {
				"id": str(job.job_id),
				"status": status,
				"data": job_data,
			},
			"timing": timing,
			"results": {
				"measurement_counts": counts_data,
			},
		}
		timing_summary = build_timing_summary(record)
		qhw_result = self.normalize_qhw("result", raw_payload)
		record["qhw_result"] = qhw_result
		record["_raw_iqm"] = raw_payload
		self._latest_cid = cid
		self._last_metadata[cid] = record
		self._last_timing[cid] = timing_summary

		return {
			"counts": counts,
			"qhw_result": qhw_result,
			"_raw_iqm": raw_payload,
		}
