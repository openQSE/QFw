from datetime import datetime, timezone
from defw_exception import DEFwExecutionError
from util.device_access import (
	QPU_DEVICE_ENV, resolve_device_access, resolve_qpu_user)
from util.iqm_transcode import (
	build_iqm_circuit, to_jsonable)
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID
import inspect
import logging
import os
import threading
import time

REQUIRED_ENV = ("QFW_QC_URL", "QFW_API_KEY")
DEFAULT_REQUEST_TIMEOUT = 30.0
DEFAULT_JOB_TIMEOUT = 300.0


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


def normalize_qhw_iqm(kind, raw_payload, device_id=None, include_raw=False):
	try:
		from qhw_iqm import normalize_calibration, normalize_coupling
		from qhw_iqm import normalize_device, normalize_result
	except Exception as exc:
		raise DEFwExecutionError(
			"failed to import qhw-iqm. Install qhw-iqm and qhw-data "
			f"before starting the IQM QPM service. Import error: {exc}") from exc

	if kind == "device":
		return normalize_device(
			raw_payload, device_id=device_id, include_raw=include_raw)
	if kind == "coupling":
		return normalize_coupling(
			raw_payload, device_id=device_id, include_raw=include_raw)
	if kind == "calibration":
		return normalize_calibration(
			raw_payload, device_id=device_id, include_raw=include_raw)
	if kind == "result":
		return normalize_result(
			raw_payload, device_id=device_id, include_raw=include_raw)
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
	normalized = value.strip().lower()
	if normalized in ("1", "true", "yes", "on"):
		return True
	if normalized in ("0", "false", "no", "off"):
		return False
	raise DEFwExecutionError(
		f"{name} must be a boolean value: {value!r}")


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


def load_iqm_service_config(credential=None):
	credential = dict(credential or {})
	if credential:
		config = {
			"url": credential.get("url") or os.environ.get("QFW_QC_URL"),
			"api_key": (
				credential.get("api_key") or
				credential.get("token") or
				os.environ.get("QFW_API_KEY")),
			"quantum_computer": (
				credential.get("quantum_computer") or
				credential.get("provider_device_id") or
				os.environ.get("QFW_IQM_QUANTUM_COMPUTER")),
			"device_id": (
				credential.get("device_id") or
				os.environ.get(QPU_DEVICE_ENV, "credential")),
			"user": credential.get("user") or resolve_qpu_user(),
		}
		if config["url"] and config["api_key"]:
			return config

	if all(os.environ.get(name) for name in REQUIRED_ENV):
		config = get_required_env()
		config["device_id"] = os.environ.get(QPU_DEVICE_ENV, "env")
		config["user"] = resolve_qpu_user()
		return config

	config = resolve_device_access(
		provider="iqm",
		device_id=credential.get("device_id"),
		user=credential.get("user"),
		credential_hint=credential.get("credential_hint"),
		credential_handle=credential.get("credential_handle"))
	if credential:
		config.update({
			key: value
			for key, value in {
				"url": credential.get("url"),
				"api_key": (
					credential.get("api_key") or credential.get("token")),
				"quantum_computer": (
					credential.get("quantum_computer") or
					credential.get("provider_device_id")),
				"device_id": credential.get("device_id"),
				"user": credential.get("user"),
			}.items()
			if value
		})
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


class IQMServiceClient:
	def __init__(self):
		self._clients = {}
		self._client_lock = threading.Lock()
		self._request_timeout = get_env_float(
			"QFW_IQM_REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)
		self._job_timeout = get_env_float(
			"QFW_IQM_JOB_TIMEOUT", DEFAULT_JOB_TIMEOUT)
		self._include_raw_results = get_env_bool(
			"QFW_IQM_INCLUDE_RAW_RESULT", True)
		self._last_metadata = {}
		self._last_timing = {}
		self._latest_cid = None
		self._config = None

	def client(self, credential=None):
		cache_key = self._credential_cache_key(credential)
		with self._client_lock:
			if cache_key in self._clients:
				return self._clients[cache_key]
			config = load_iqm_service_config(credential=credential)
			client_type = load_iqm_client_module()
			client = create_iqm_client(client_type, config)
			self._clients[cache_key] = client
			self._config = config
			logging.debug(
				"created IQM client for "
				f"{sanitize_url(config['url'])} as {config['user']}")
			return client

	def _credential_cache_key(self, credential=None):
		credential = dict(credential or {})
		if not credential:
			return ("default",)
		return (
			credential.get("url"),
			credential.get("provider_device_id"),
			credential.get("device_id"),
			credential.get("user"),
			credential.get("api_key") or credential.get("token"),
		)

	def device_id(self):
		if self._config is None:
			self.client()
		if self._config:
			return self._config.get("device_id")
		return os.environ.get(QPU_DEVICE_ENV)

	def normalize_qhw(self, kind, raw_payload, include_raw=False):
		return normalize_qhw_iqm(
			kind, raw_payload, device_id=self.device_id(),
			include_raw=include_raw)

	def get_static_architecture(self, credential=None):
		return call_iqm_method(
			self.client(credential=credential).get_static_quantum_architecture,
			self._request_timeout)

	def get_dynamic_architecture(self, calibration_set_id=None,
				     credential=None):
		calibration_set_id = parse_calibration_set_id(calibration_set_id)
		return call_iqm_method(
			self.client(credential=credential).get_dynamic_quantum_architecture,
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

	def get_task_timing(self, cid=None):
		if cid is None:
			cid = self._latest_cid
		return self._last_timing.get(cid, {
			"backend": "iqm",
			"cid": cid,
			"metadata_supported": True,
			"timing_available": False,
		})

	def get_task_metadata(self, cid=None):
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
		credential = getattr(circ, "provider_credential", None)
		client = self.client(credential=credential)
		calibration_set_id = parse_calibration_set_id(
			info.get("calibration_set_id")
			or info.get("iqm_calibration_set_id"))
		use_timeslot = bool(info.get("use_timeslot", False))
		shots = int(info.get("num_shots", info.get("shots", 1)))
		timeout = float(info.get("timeout", self._job_timeout))
		mapping = info.get("iqm_qubit_mapping") or info.get("qubit_mapping")

		timing = {}
		start = time.monotonic()
		dynamic = self.get_dynamic_architecture(
			calibration_set_id, credential=credential)
		iqm_circuit = build_iqm_circuit(
			info["qasm"],
			dynamic,
			mapping,
			client=client,
			calibration_set_id=calibration_set_id,
		)
		effective_calibration_set_id = calibration_set_id
		circuit_metadata = getattr(iqm_circuit, "metadata", None)
		if (effective_calibration_set_id is None and
				isinstance(circuit_metadata, dict)):
			effective_calibration_set_id = circuit_metadata.get(
				"iqm_calibration_set_id")
		run_request = client.create_run_request(
			[iqm_circuit],
			calibration_set_id=effective_calibration_set_id,
			shots=shots)

		submit_started = time.monotonic()
		job = submit_run_request(client, run_request, use_timeslot)
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
		measurement_counts = client.get_job_measurement_counts(job.job_id)
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
		qhw_result = self.normalize_qhw(
			"result", raw_payload, include_raw=self._include_raw_results)
		record["qhw_result"] = qhw_result
		self._latest_cid = cid
		self._last_metadata[cid] = record
		self._last_timing[cid] = timing_summary

		return {
			"counts": counts,
			"qhw_result": qhw_result,
		}
