from defw_exception import DEFwExecutionError
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from util.circuit_payload import qiskit_input
from util.device_access import (
	QPU_DEVICE_ENV, resolve_device_access, resolve_qpu_user)
import json
import logging
import math
import os
import threading
import time

import numpy as np

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_DIR = os.path.join(PACKAGE_DIR, "configs")
DEFAULT_JOB_TIMEOUT = 300.0
DEFAULT_WAIT_DONE_TIMEOUT = 10.0

# qblox-scheduler is imported lazily inside the agent build, so the service
# module can be loaded (and queried) on a node without the stack installed.
REQUIRED_PACKAGES = ("qblox-scheduler", "qblox-instruments")

# Short names map to their QBLOX_* equivalents.
ENV_ALIASES = {
	"CLUSTER_IP": "QBLOX_CLUSTER_IP",
	"CLUSTER_NAME": "QBLOX_CLUSTER_NAME",
	"HARDWARE_CONFIG": "QBLOX_HARDWARE_CONFIG",
	"DEVICE_CONFIG": "QBLOX_DEVICE_CONFIG",
	"PROFILE_STAGES": "QBLOX_PROFILE_STAGES",
}

# An env file cannot hold a real None; these spellings mean "not set".
NULL_VALUES = {"", "none", "null", "~"}


def strip_quotes(value):
	# The site environment can carry quoted values through the manifest.
	return value.strip().strip('"') if isinstance(value, str) else value


def parse_env_value(raw):
	# Quoted values keep their contents verbatim and anything after the
	# closing quote is a comment; unquoted values end at the first ' #'.
	raw = raw.strip()
	if raw[:1] in ("'", '"'):
		quote = raw[0]
		end = raw.find(quote, 1)
		if end != -1:
			return raw[1:end]
		return raw[1:]
	for marker in (" #", "\t#"):
		index = raw.find(marker)
		if index != -1:
			raw = raw[:index]
			break
	return raw.strip()


def parse_env_file(path):
	# Small KEY=value reader: python-dotenv is not a QFw dependency.
	values = {}
	with open(path, "r") as handle:
		for line in handle:
			line = line.strip()
			if not line or line.startswith("#"):
				continue
			if line.startswith("export "):
				line = line[len("export "):]
			if "=" not in line:
				continue
			key, _, raw = line.partition("=")
			key = key.strip().upper()
			if not key:
				continue
			key = ENV_ALIASES.get(key, key)
			values[key] = parse_env_value(raw)
	return values


def env_file_path():
	configured = strip_quotes(os.environ.get("QBLOX_ENV_FILE"))
	if configured:
		return configured
	# Only alongside the service package: a QFw service starts in a
	# generated run directory, so a CWD-relative .env would be a trap.
	default = os.path.join(PACKAGE_DIR, ".env")
	return default if os.path.exists(default) else None


def load_env_file(path=None, override=False):
	"""
	Apply a .env file to os.environ and return what it set.

	The real process environment wins by default: the site manifest,
	device-access config and anything exported before qfw-setup are the
	operator's decision, and a file sitting in the service package must not
	silently override them.
	"""
	path = path or env_file_path()
	if not path:
		return {}
	if not os.path.exists(path):
		raise DEFwExecutionError(f"QBLOX_ENV_FILE does not exist: {path}")

	applied = {}
	for key, value in parse_env_file(path).items():
		if value.strip().lower() in NULL_VALUES:
			# "cluster_ip = None" means the dummy cluster, so leave the
			# variable unset rather than setting it to the string "None".
			if override:
				os.environ.pop(key, None)
			continue
		if not override and key in os.environ:
			continue
		os.environ[key] = value
		applied[key] = value
	if applied:
		logging.info(
			f"loaded Qblox environment from {path}: "
			f"{', '.join(sorted(applied))}")
	return applied


load_env_file()


def get_env_float(name, default):
	value = os.environ.get(name)
	if not value:
		return default
	try:
		return float(value)
	except ValueError as exc:
		raise DEFwExecutionError(
			f"{name} must be a floating point number: {value!r}") from exc


def get_env_bool(name, default):
	value = os.environ.get(name)
	if value is None or not value.strip():
		return default
	normalized = value.strip().strip('"').lower()
	if normalized in ("1", "true", "yes", "on"):
		return True
	if normalized in ("0", "false", "no", "off"):
		return False
	raise DEFwExecutionError(f"{name} must be a boolean value: {value!r}")


def check_qblox_packages():
	missing = []
	for name in REQUIRED_PACKAGES:
		try:
			package_version(name)
		except PackageNotFoundError:
			missing.append(name)
	if missing:
		raise DEFwExecutionError(
			f"missing required package(s): {', '.join(missing)}. QFw Qblox "
			"services require the qblox-scheduler stack in the QFw venv.")


def resolve_config_path(value, default_name):
	# A QFw service starts in a generated run directory, and in a multinode
	# allocation it starts on a different node than the one you launched from.
	# Config paths must therefore be absolute and node-visible.
	value = strip_quotes(value)
	if not value:
		return os.path.join(DEFAULT_CONFIG_DIR, default_name)
	if not os.path.isabs(value):
		return os.path.join(DEFAULT_CONFIG_DIR, value)
	return value


def read_json_config(path, label):
	try:
		with open(path, "r") as handle:
			return json.load(handle)
	except OSError as exc:
		raise DEFwExecutionError(
			f"could not read Qblox {label} config at {path}: {exc}") from exc
	except ValueError as exc:
		raise DEFwExecutionError(
			f"Qblox {label} config at {path} is not valid JSON: {exc}") from exc


def device_qubit_count(device_config_path=None):
	# Read before any HardwareAgent exists: the agent mutates device_cfg in
	# place (it empties "elements"), so counting afterwards silently gives 0.
	# svc_info needs this statically, before the QPM is constructed.
	path = resolve_config_path(
		device_config_path or os.environ.get("QBLOX_DEVICE_CONFIG"),
		"dev_cfg.json")
	try:
		device_cfg = read_json_config(path, "device")
	except DEFwExecutionError:
		logging.warning(
			f"could not read Qblox device config at {path} for a qubit "
			"count; falling back to 0")
		return 0
	return len(device_cfg.get("elements", {}))


def load_qblox_service_config(credential=None):
	credential = dict(credential or {})
	if credential:
		config = {
			"cluster_ip": (
				credential.get("cluster_ip") or
				credential.get("url") or
				strip_quotes(os.environ.get("QBLOX_CLUSTER_IP"))),
			"cluster_name": (
				credential.get("cluster_name") or
				strip_quotes(os.environ.get("QBLOX_CLUSTER_NAME")) or
				"cluster0"),
			"hardware_config": resolve_config_path(
				credential.get("hardware_config") or
				os.environ.get("QBLOX_HARDWARE_CONFIG"), "hw_cfg.json"),
			"device_config": resolve_config_path(
				credential.get("device_config") or
				os.environ.get("QBLOX_DEVICE_CONFIG"), "dev_cfg.json"),
			"device_id": (
				credential.get("device_id") or
				os.environ.get(QPU_DEVICE_ENV, "credential")),
			"user": credential.get("user") or resolve_qpu_user(),
		}
		if config["cluster_ip"]:
			return config

	cluster_ip = strip_quotes(os.environ.get("QBLOX_CLUSTER_IP"))
	if cluster_ip:
		return {
			"cluster_ip": cluster_ip,
			"cluster_name": strip_quotes(
				os.environ.get("QBLOX_CLUSTER_NAME")) or "cluster0",
			"hardware_config": resolve_config_path(
				os.environ.get("QBLOX_HARDWARE_CONFIG"), "hw_cfg.json"),
			"device_config": resolve_config_path(
				os.environ.get("QBLOX_DEVICE_CONFIG"), "dev_cfg.json"),
			"device_id": os.environ.get(QPU_DEVICE_ENV, "env"),
			"user": resolve_qpu_user(),
		}

	# Service manifests have no environment block, so device-access.yaml is
	# the only way a site-deployed cluster's address reaches the service.
	# QFw sets QFW_QPU_DEVICE_ID from device-id and points
	# QFW_DEVICE_ACCESS_CFG at the file.
	device_id = credential.get("device_id") or os.environ.get(QPU_DEVICE_ENV)
	if device_id and os.environ.get("QFW_DEVICE_ACCESS_CFG"):
		access = resolve_device_access(
			provider="qblox",
			device_id=device_id,
			user=credential.get("user"),
			credential_hint=credential.get("credential_hint"),
			credential_handle=credential.get("credential_handle"))
		if access.get("url"):
			return {
				"cluster_ip": access.get("url"),
				"cluster_name": (
					access.get("provider_device_id") or "cluster0"),
				"hardware_config": resolve_config_path(
					os.environ.get("QBLOX_HARDWARE_CONFIG"), "hw_cfg.json"),
				"device_config": resolve_config_path(
					os.environ.get("QBLOX_DEVICE_CONFIG"), "dev_cfg.json"),
				"device_id": access.get("device_id"),
				"user": access.get("user"),
			}

	# No cluster address anywhere selects the dummy cluster.
	return {
		"cluster_ip": None,
		"cluster_name": strip_quotes(
			os.environ.get("QBLOX_CLUSTER_NAME")) or "cluster0",
		"hardware_config": resolve_config_path(
			os.environ.get("QBLOX_HARDWARE_CONFIG"), "hw_cfg.json"),
		"device_config": resolve_config_path(
			os.environ.get("QBLOX_DEVICE_CONFIG"), "dev_cfg.json"),
		"device_id": os.environ.get(QPU_DEVICE_ENV, "dummy"),
		"user": resolve_qpu_user(),
	}


def rad_to_deg(radians):
	return radians * 180.0 / math.pi


def build_schedule(circuit, shots):
	"""
	Basis-gate rewrite to rx+ry+cz, not u/rz+rx: this hardware has no NCO,
	and Rz only means anything as an NCO phase shift (ShiftClockPhase).
	rx and ry are two non-parallel axes, so Qiskit can synthesise any
	single-qubit gate from them alone (Euler's rotation theorem), which
	makes this valid for arbitrary circuits, not just GHZ.
	"""
	from qiskit.compiler import transpile as qiskit_transpile
	from qblox_scheduler import Schedule
	from qblox_scheduler.operations import Reset, Rxy, CZ, Measure

	adapted = qiskit_transpile(
		circuit, basis_gates=["rx", "ry", "cz"], optimization_level=1)
	qubit_names = [f"q{i}" for i in range(adapted.num_qubits)]

	schedule = Schedule(f"circuit_{id(circuit)}", repetitions=shots)
	schedule.add(Reset(*qubit_names))

	for instruction in adapted.data:
		gate_name = instruction.operation.name
		qubits = [adapted.qubits.index(q) for q in instruction.qubits]
		params = instruction.operation.params

		if gate_name == "rx":
			schedule.add(Rxy(
				theta=rad_to_deg(params[0]), phi=0.0,
				qubit=qubit_names[qubits[0]]))
		elif gate_name == "ry":
			schedule.add(Rxy(
				theta=rad_to_deg(params[0]), phi=90.0,
				qubit=qubit_names[qubits[0]]))
		elif gate_name == "cz":
			schedule.add(CZ(qubit_names[qubits[0]], qubit_names[qubits[1]]))
		elif gate_name == "measure":
			# bin_mode="append" gives one value per repetition; the
			# default averages them into a single bin.
			# ThresholdedAcquisition makes the FPGA apply dev_cfg's
			# acq_rotation/acq_threshold and return a 0/1 bit - without it
			# this returns raw SSB integration amplitude instead.
			schedule.add(Measure(
				qubit_names[qubits[0]], acq_channel=qubits[0], acq_index=0,
				bin_mode="append", acq_protocol="ThresholdedAcquisition"))
		elif gate_name == "barrier":
			pass
		else:
			raise DEFwExecutionError(
				f"unsupported gate {gate_name!r} after basis rewrite")

	return schedule


def extract_channel_values(dataset, num_qubits):
	"""
	Pull one array per acq_channel (qubit) out of the dataset returned by
	HardwareAgent.run(). Tries int then str keys; fails loudly with the
	dataset's real variable names otherwise, rather than reading the wrong
	channel silently.
	"""
	channel_values = {}
	for qubit_idx in range(num_qubits):
		if qubit_idx in dataset.data_vars:
			var = dataset[qubit_idx]
		elif str(qubit_idx) in dataset.data_vars:
			var = dataset[str(qubit_idx)]
		else:
			raise DEFwExecutionError(
				f"acq_channel {qubit_idx} not found in dataset. Available "
				f"data variables: {list(dataset.data_vars)}")
		channel_values[qubit_idx] = np.asarray(var.values).squeeze()
	return channel_values


def build_counts(channel_values, shots):
	# Thresholded acquisition returns -1 for |0> and +1 for |1>, not 0/1.
	num_qubits = len(channel_values)
	counts = {}
	for shot in range(shots):
		bitstring = "".join(
			"1" if float(channel_values[q][shot]) > 0 else "0"
			for q in range(num_qubits))
		counts[bitstring] = counts.get(bitstring, 0) + 1
	return counts


class QbloxServiceClient:
	"""
	Owns the Qblox control stack for the QPM: one HardwareAgent, built
	lazily, and an explicit lock around the execute path.

	The lock is not optional. HardwareAgent and instrument_coordinator
	stop()/prepare()/start() are global device operations, so two circuits
	executing concurrently would corrupt each other's acquisition.
	qhw-scheduler is told the same thing via concurrent_jobs=1.
	"""

	def __init__(self):
		self._agent = None
		self._agent_lock = threading.Lock()
		self._execute_lock = threading.Lock()
		self._config = None
		self._num_qubits = 0
		self._job_timeout = get_env_float(
			"QFW_QBLOX_JOB_TIMEOUT", DEFAULT_JOB_TIMEOUT)
		self._wait_done_timeout = get_env_float(
			"QFW_QBLOX_WAIT_DONE_TIMEOUT", DEFAULT_WAIT_DONE_TIMEOUT)
		self._profile_stages = get_env_bool("QBLOX_PROFILE_STAGES", False)
		self._last_metadata = {}
		self._last_timing = {}
		self._latest_cid = None

	def agent(self, credential=None):
		with self._agent_lock:
			if self._agent is not None:
				return self._agent

			check_qblox_packages()
			config = load_qblox_service_config(credential=credential)

			if config["cluster_ip"] is None:
				logging.info(
					"configuring HardwareAgent in DUMMY mode "
					"(no cluster IP) - qblox-scheduler's built-in software "
					"stub, no real device involved")
			else:
				logging.info(
					"configuring HardwareAgent for REAL Cluster at "
					f"{config['cluster_ip']} - commands below reach "
					"physical hardware")

			hardware_cfg = read_json_config(
				config["hardware_config"], "hardware")

			# Find the Cluster entry explicitly rather than assuming key order.
			cluster_key = None
			for key, desc in hardware_cfg["hardware_description"].items():
				if desc.get("instrument_type") == "Cluster":
					cluster_key = key
					break
			if cluster_key is None:
				raise DEFwExecutionError(
					"no instrument_type='Cluster' found in "
					"hardware_description. Keys found: "
					f"{list(hardware_cfg['hardware_description'].keys())}")
			hardware_cfg["hardware_description"][cluster_key]["ip"] = \
				config["cluster_ip"]

			device_cfg = read_json_config(config["device_config"], "device")
			num_qubits = len(device_cfg.get("elements", {}))

			from qblox_scheduler import HardwareAgent
			agent = HardwareAgent(hardware_cfg, device_cfg)

			self._agent = agent
			self._config = config
			self._num_qubits = num_qubits
			logging.info(f"HardwareAgent ready: {num_qubits} qubits")
			return self._agent

	def device_id(self):
		if self._config is None:
			return os.environ.get(QPU_DEVICE_ENV)
		return self._config.get("device_id")

	def num_qubits(self):
		if self._num_qubits:
			return self._num_qubits
		return device_qubit_count()

	def is_dummy(self):
		if self._config is None:
			return not strip_quotes(os.environ.get("QBLOX_CLUSTER_IP"))
		return self._config.get("cluster_ip") is None

	def evict_reservation(self, reservation_id):
		# One agent bound to one instrument: no per-reservation client to
		# drop.
		return False

	def _run_staged(self, agent, circuit, shots):
		"""
		Manually replicates qblox_scheduler.experiments.schedules.
		ExecuteSchedule.run() - what agent.run() calls internally - timing
		each step separately: agent.run() only exposes the total. Returns
		(dataset, stage_timings_ms).
		"""
		from qblox_scheduler.backends.graph_compilation import SerialCompiler

		timings_ms = {}

		def timed(label, fn, *args, **kwargs):
			start = time.time()
			result = fn(*args, **kwargs)
			timings_ms[label] = (time.time() - start) * 1000
			return result

		schedule = timed("schedule", build_schedule, circuit, shots)

		agent.connect_clusters()

		compiler = SerialCompiler()
		compiled_schedule = timed(
			"compile", compiler.compile,
			schedule=schedule,
			config=agent.quantum_device.generate_compilation_config())
		# Matches agent.compile()'s own bookkeeping.
		agent._latest_compiled_schedule = compiled_schedule

		inst_coord = agent.instrument_coordinator
		timed("stop", inst_coord.stop, allow_failure=True)
		timed("prepare", inst_coord.prepare, compiled_schedule)
		timed("start", inst_coord.start)
		timed("wait_done", inst_coord.wait_done,
		      timeout_sec=int(self._wait_done_timeout))
		dataset = timed("retrieve_acquisition", inst_coord.retrieve_acquisition)

		timings_ms["total"] = sum(timings_ms.values())
		return dataset, timings_ms

	def _log_compiled_schedule(self, agent):
		"""
		agent.run() populates latest_compiled_schedule as a side effect.
		Logs the per-operation timing table at DEBUG; compiled_instructions
		holds the per-sequencer Q1ASM.
		"""
		compiled = getattr(agent, "latest_compiled_schedule", None)
		if compiled is None:
			return
		logging.debug(f"compiled schedule timing table:\n{compiled.timing_table}")

	def run_circuit(self, circ):
		info = circ.info
		cid = circ.get_cid()
		credential = getattr(circ, "provider_credential", None)
		agent = self.agent(credential=credential)

		shots = int(info.get("num_shots", info.get("shots", 1024)))
		circuit = qiskit_input(info)
		if isinstance(circuit, str):
			raise DEFwExecutionError(
				"this QPM reads circuits with Qiskit and received OpenQASM 2 "
				"text it could not load; submit QPY instead")

		if circuit.num_qubits > self.num_qubits():
			raise DEFwExecutionError(
				f"circuit needs {circuit.num_qubits} qubits; this device has "
				f"{self.num_qubits()}")

		start = time.monotonic()
		# Everything that touches the instrument happens under one lock.
		with self._execute_lock:
			queue_wait = time.monotonic() - start
			exec_started = time.monotonic()
			stage_timings_ms = None
			if self._profile_stages:
				dataset, stage_timings_ms = self._run_staged(
					agent, circuit, shots)
			else:
				schedule = build_schedule(circuit, shots)
				dataset = agent.run(schedule)
			self._log_compiled_schedule(agent)
			exec_seconds = time.monotonic() - exec_started

		# Raw per-shot values distinguish "acquisition varies but always
		# thresholds the same way" from "acquisition is not varying".
		channel_values = extract_channel_values(dataset, circuit.num_qubits)
		counts = build_counts(channel_values, shots)
		total_seconds = time.monotonic() - start

		timing = {
			"schema": "qblox-timing-summary-v1",
			"cid": cid,
			"backend": "qblox",
			"metadata_supported": True,
			"timing_available": True,
			"client_wall_seconds": {
				"queue_wait": queue_wait,
				"execute": exec_seconds,
				"total": total_seconds,
			},
			"stage_timings_ms": stage_timings_ms,
		}
		metadata = {
			"cid": cid,
			"backend": "qblox",
			"metadata_supported": True,
			"metadata_available": True,
			"mode": "dummy" if self.is_dummy() else "cluster",
			"device_id": self.device_id(),
			"input": {
				"shots": shots,
				"num_qubits": circuit.num_qubits,
			},
			"results": {
				"counts": counts,
				"per_shot_channel_values": {
					qubit: values.tolist()
					for qubit, values in channel_values.items()
				},
			},
			"timing": timing,
		}

		self._latest_cid = cid
		self._last_timing[cid] = timing
		self._last_metadata[cid] = metadata

		logging.info(
			f"circuit executed: {circuit.num_qubits}q, {shots} shots, "
			f"{total_seconds:.2f}s, top counts: "
			f"{dict(list(counts.items())[:5])}")

		return {
			"counts": counts,
			"qblox": {
				"mode": metadata["mode"],
				"device_id": metadata["device_id"],
				"per_shot_channel_values":
					metadata["results"]["per_shot_channel_values"],
				"stage_timings_ms": stage_timings_ms,
			},
		}

	def get_backend_info(self):
		return {
			"backend": "qblox",
			"metadata_supported": True,
			"mode": "dummy" if self.is_dummy() else "cluster",
			"num_qubits": self.num_qubits(),
			"device_id": self.device_id(),
		}

	def get_device_info(self):
		return self.get_backend_info()

	def get_dynamic_backend_info(self, calibration_set_id=None):
		# Qblox calibration lives in dev_cfg.json, not a server-side
		# calibration set, so there is no calibration-set id to honour.
		info = self.get_backend_info()
		info["calibration_set_id"] = calibration_set_id
		info["calibration_source"] = "device_config"
		return info

	def get_calibration_snapshot(self, calibration_set_id=None):
		config = self._config or load_qblox_service_config()
		device_cfg = read_json_config(config["device_config"], "device")
		elements = device_cfg.get("elements", {})
		return {
			"backend": "qblox",
			"calibration_set_id": calibration_set_id,
			"calibration_source": "device_config",
			"device_config_path": config["device_config"],
			"elements": {
				name: {
					key: value
					for key, value in (element.get("measure") or {}).items()
					if key in ("acq_rotation", "acq_threshold",
						   "acq_delay", "pulse_amp")
				}
				for name, element in elements.items()
			},
		}

	def get_coupling_graph(self, calibration_set_id=None):
		config = self._config or load_qblox_service_config()
		device_cfg = read_json_config(config["device_config"], "device")
		elements = list(device_cfg.get("elements", {}).keys())
		index_of = {name: index for index, name in enumerate(elements)}
		couplings = []
		for edge in device_cfg.get("edges", {}):
			parts = str(edge).split("_")
			if len(parts) == 2 and all(part in index_of for part in parts):
				couplings.append([index_of[parts[0]], index_of[parts[1]]])
		return {
			"backend": "qblox",
			"calibration_set_id": calibration_set_id,
			"qubits": elements,
			"couplings": couplings,
		}

	def get_task_timing(self, cid=None):
		if cid is None:
			cid = self._latest_cid
		return self._last_timing.get(cid, {
			"backend": "qblox",
			"cid": cid,
			"metadata_supported": True,
			"timing_available": False,
		})

	def get_task_metadata(self, cid=None):
		if cid is None:
			cid = self._latest_cid
		return self._last_metadata.get(cid, {
			"backend": "qblox",
			"cid": cid,
			"metadata_supported": True,
			"metadata_available": False,
		})
