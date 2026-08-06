import inspect


QHW_ADM_THREAD_SAFE = "QHW_ADM_THREAD_SAFE"
QHW_ADM_THREAD_USER = "QHW_ADM_THREAD_USER"
DEFAULT_WORKLOAD_KIND = "QHW_ADM_WORKLOAD_QUANTUM_JOB"
DECISION_ACCEPTED = "accepted"
DECISION_DELAYED = "delayed"
DECISION_REJECTED = "rejected"


class QPMAdmissionUnavailable(RuntimeError):
	pass


class QPMAdmissionValidationError(RuntimeError):
	pass


class QPMAdmissionPendingCapacity(RuntimeError):
	pass


class UnavailableAdmissionContext:
	def __init__(self, threading_mode, error):
		self.threading_mode = threading_mode
		self.error = error
		self.available = False

	@property
	def threading(self):
		return self.threading_mode

	def close(self):
		return None

	def __getattr__(self, name):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {self.error}")


def create_admission_context(threading_mode=QHW_ADM_THREAD_SAFE):
	try:
		import qhw_admission
	except Exception as error:
		return UnavailableAdmissionContext(threading_mode, error)

	threading_value = _native_threading_value(qhw_admission, threading_mode)
	return qhw_admission.AdmissionContext(threading=threading_value)


def admission_context_available(context):
	return getattr(context, "available", True)


def register_device_profile(context, profile):
	if not admission_context_available(context):
		return None
	handler = getattr(context, "register_device_profile", None)
	if handler is not None:
		return handler(dict(profile))

	qhw_admission = _import_qhw_admission()
	baseline_info = profile.get("baseline", {})
	baseline = qhw_admission.Baseline(
		qubit_count=baseline_info.get("qubit_count", 1),
		depth=baseline_info.get("depth", 1),
		one_q_gate_count=baseline_info.get("one_q_gate_count", 0),
		two_q_gate_count=baseline_info.get("two_q_gate_count", 0),
		shots=baseline_info.get("shots", 1),
		measurement_count=baseline_info.get("measurement_count", 1),
	)
	profile_kwargs = {
		"device_id": profile["device_id"],
		"baseline": baseline,
		"max_qubits": profile.get("max_qubits", 0),
		"one_q_gate_ns": profile.get("one_q_gate_ns", 0),
		"two_q_gate_ns": profile.get("two_q_gate_ns", 0),
		"measurement_ns": profile.get("measurement_ns", 0),
		"time_span_ns": profile.get("time_span_ns", 0),
		"max_shots": profile.get("max_shots", 0),
		"one_q_gate_transfer_ns": profile.get("one_q_gate_transfer_ns", 0),
		"two_q_gate_transfer_ns": profile.get("two_q_gate_transfer_ns", 0),
		"measurement_transfer_ns": profile.get("measurement_transfer_ns", 0),
		"compile_ns": profile.get("compile_ns", 0),
		"control_overhead_ns": profile.get("control_overhead_ns", 0),
		"provider_overhead_ns": profile.get("provider_overhead_ns", 0),
		"total_credits": profile.get("total_credits", 0),
		"device_rate": profile.get("device_rate", 0),
		"concurrent_jobs": profile.get("concurrent_jobs", 0),
		"default_ttl_ns": profile.get("default_ttl_ns", 0),
		"max_provider_queue_depth": profile.get(
			"max_provider_queue_depth", 0),
	}
	device_profile = qhw_admission.DeviceProfile(
		**_supported_kwargs(qhw_admission.DeviceProfile, profile_kwargs))
	return context.register_device(device_profile)


def set_policy(context, device_id, policy, estimator=None,
	       device_profile=None):
	if not admission_context_available(context):
		return None
	handler = getattr(context, "set_admission_policy", None)
	if handler is not None:
		return handler(device_id, dict(policy))

	policy_path = policy.get("path")
	if policy_path:
		context.add_policy_path(policy_path)
	policy_name = (
		policy.get("name") or
		policy.get("policy") or
		policy.get("policy_name"))
	if policy_name:
		options = _policy_options(policy)
		if options and not _setter_accepts_options(context.set_policy):
			return _load_device_policy_config(
				context, device_id, policy=policy, estimator=estimator,
				device_profile=device_profile)
		return _call_policy_setter(
			context.set_policy, device_id, policy_name, options)
	return None


def set_estimator(context, device_id, estimator, policy=None,
		  device_profile=None):
	if not admission_context_available(context):
		return None
	handler = getattr(context, "set_estimator_policy", None)
	if handler is not None:
		return handler(device_id, dict(estimator))

	estimator_path = estimator.get("path")
	if estimator_path:
		context.add_estimator_path(estimator_path)
	estimator_name = (
		estimator.get("name") or
		estimator.get("estimator") or
		estimator.get("estimator_name"))
	if estimator_name:
		options = _estimator_options(estimator)
		if options and not _setter_accepts_options(context.set_estimator):
			return _load_device_policy_config(
				context, device_id, policy=policy, estimator=estimator,
				device_profile=device_profile)
		return _call_policy_setter(
			context.set_estimator, device_id, estimator_name, options)
	return None


def evaluate_request(context, request):
	handler = _handler(context, "evaluate_request")
	if handler is not None:
		return _decision_dict(handler(dict(request)))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")

	qhw_admission = _import_qhw_admission()
	native_request = _native_admission_request(qhw_admission, request)
	try:
		return _decision_dict(context.evaluate(native_request))
	finally:
		native_request.close()


def reserve_request(context, request):
	handler = _handler(context, "reserve_request")
	if handler is not None:
		return _decision_dict(handler(dict(request)))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")

	qhw_admission = _import_qhw_admission()
	native_request = _native_admission_request(qhw_admission, request)
	try:
		return _decision_dict(context.reserve(native_request))
	finally:
		native_request.close()


def renew_reservation(context, reservation_id, request):
	handler = _handler(context, "renew_reservation")
	if handler is not None:
		return _decision_dict(handler(reservation_id, dict(request or {})))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")

	now_ns = (request or {}).get("now_ns", 0)
	ttl_ns = (request or {}).get("ttl_ns", 0)
	context.renew(reservation_id, now_ns, ttl_ns)
	return {
		"status": DECISION_ACCEPTED,
		"reservation_id": reservation_id,
	}


def release_reservation(context, reservation_id, reason_code=0):
	handler = _handler(context, "release_reservation")
	if handler is not None:
		return _decision_dict(handler(reservation_id, reason_code))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")

	context.release(reservation_id, reason_code)
	return {
		"status": DECISION_ACCEPTED,
		"reservation_id": reservation_id,
	}


def cancel_reservation(context, reservation_id, reason_code=0):
	handler = _handler(context, "cancel_reservation")
	if handler is not None:
		return _decision_dict(handler(reservation_id, reason_code))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")

	context.cancel(reservation_id, reason_code)
	return {
		"status": DECISION_ACCEPTED,
		"reservation_id": reservation_id,
	}


def expire_reservations(context, now_ns=0):
	handler = _handler(context, "expire_reservations")
	if handler is not None:
		return handler(now_ns)
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")
	return context.expire(now_ns)


def get_reservation(context, reservation_id):
	handler = _handler(context, "get_reservation_record")
	if handler is not None:
		return _reservation_dict(handler(reservation_id))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")
	return _reservation_dict(context.get_reservation(reservation_id))


def list_reservations(context, filters=None):
	filters = dict(filters or {})
	handler = _handler(context, "list_reservation_records")
	if handler is not None:
		return [_reservation_dict(item) for item in handler(filters)]
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")
	return [
		_reservation_dict(item)
		for item in context.list_reservations(**filters)
	]


def authorize_usage(context, reservation_id, usage):
	handler = _handler(context, "authorize_usage_request")
	if handler is not None:
		return _decision_dict(handler(reservation_id, dict(usage)))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")
	qhw_admission = _import_qhw_admission()
	return _decision_dict(
		context.authorize_usage(
			reservation_id,
			_native_usage(qhw_admission, reservation_id, usage)))


def consume_usage(context, reservation_id, usage):
	handler = _handler(context, "consume_usage_request")
	if handler is not None:
		return _decision_dict(handler(reservation_id, dict(usage)))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")
	qhw_admission = _import_qhw_admission()
	return _decision_dict(
		context.consume(
			reservation_id,
			_native_usage(qhw_admission, reservation_id, usage)))


def return_usage(context, reservation_id, usage):
	handler = _handler(context, "return_usage_request")
	if handler is not None:
		return handler(reservation_id, dict(usage))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")
	qhw_admission = _import_qhw_admission()
	return context.return_usage(
		reservation_id,
		_native_usage(qhw_admission, reservation_id, usage))


def record_actual(context, reservation_id, actual):
	handler = _handler(context, "record_actual_request")
	if handler is not None:
		return handler(reservation_id, dict(actual))
	if not admission_context_available(context):
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {context.error}")
	qhw_admission = _import_qhw_admission()
	return context.record_actual(
		reservation_id,
		qhw_admission.ActualUsage(
			reservation_id=reservation_id,
			task_id=actual.get("task_id", 0),
			observed_device_ns=actual.get("observed_device_ns", 0),
			observed_compile_ns=actual.get("observed_compile_ns", 0),
			observed_transfer_ns=actual.get("observed_transfer_ns", 0),
			observed_control_overhead_ns=actual.get(
				"observed_control_overhead_ns", 0),
		))


def _native_threading_value(qhw_admission, threading_mode):
	if threading_mode == QHW_ADM_THREAD_SAFE:
		return qhw_admission.THREAD_SAFE
	if threading_mode == QHW_ADM_THREAD_USER:
		return qhw_admission.THREAD_USER
	raise ValueError(f"unsupported qhw-admission threading mode: {threading_mode}")


def _import_qhw_admission():
	try:
		import qhw_admission
	except Exception as error:
		raise QPMAdmissionUnavailable(
			f"qhw-admission context is unavailable: {error}")
	return qhw_admission


def _supported_kwargs(callable_object, values):
	parameters = inspect.signature(callable_object).parameters
	return {key: value for key, value in values.items() if key in parameters}


def _handler(context, name):
	if not admission_context_available(context):
		return None
	return getattr(context, name, None)


def _setter_accepts_options(setter):
	try:
		parameters = inspect.signature(setter).parameters.values()
	except (TypeError, ValueError):
		return False
	positional_count = 0
	for parameter in parameters:
		if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
			return True
		if parameter.name == "options":
			return True
		if parameter.kind in (
				inspect.Parameter.POSITIONAL_ONLY,
				inspect.Parameter.POSITIONAL_OR_KEYWORD):
			positional_count += 1
	return positional_count >= 3


def _call_policy_setter(setter, device_id, name, options):
	if options:
		return setter(device_id, name, options)
	return setter(device_id, name)


def _policy_options(policy):
	policy = dict(policy or {})
	return _plain_options(
		policy.get("options") or policy.get("policy_options") or {})


def _estimator_options(estimator):
	estimator = dict(estimator or {})
	return _plain_options(
		estimator.get("options") or
		estimator.get("estimator_options") or
		{})


def _plain_options(options):
	if not options:
		return {}
	if isinstance(options, dict):
		return dict(options)
	result = {}
	for item in options:
		if isinstance(item, dict) and "key" in item and "value" in item:
			result[item["key"]] = item["value"]
		elif isinstance(item, (list, tuple)) and len(item) == 2:
			result[item[0]] = item[1]
	return result


def _load_device_policy_config(context, device_id, policy=None,
			       estimator=None, device_profile=None):
	loader = getattr(context, "load_config_string", None)
	if loader is None:
		raise QPMAdmissionValidationError(
			"qhw-admission context cannot apply policy options")
	profile = _device_profile_dict(device_profile)
	if profile is None:
		getter = getattr(context, "get_device", None)
		if getter is not None:
			profile = _device_profile_dict(getter(device_id))
	if profile is None:
		raise QPMAdmissionValidationError(
			"admission policy options require a device profile")
	if device_id is not None:
		profile["device_id"] = device_id
	return loader(_device_policy_config_yaml(profile, policy, estimator))


def _device_policy_config_yaml(profile, policy=None, estimator=None):
	lines = [
		"devices:",
		f"  - device_id: {_yaml_scalar(profile['device_id'])}",
		f"    max_qubits: {_yaml_scalar(_profile_value(profile, 'max_qubits'))}",
		f"    max_shots: {_yaml_scalar(_profile_value(profile, 'max_shots'))}",
		"    max_provider_queue_depth: " +
		_yaml_scalar(_profile_value(profile, "max_provider_queue_depth")),
		f"    time_span_ns: {_yaml_scalar(_profile_value(profile, 'time_span_ns'))}",
		"    default_ttl_ns: " +
		_yaml_scalar(_profile_value(profile, "default_ttl_ns")),
		"    baseline:",
	]
	baseline = _profile_value(profile, "baseline", {})
	for key in (
			"qubit_count", "depth", "one_q_gate_count",
			"two_q_gate_count", "measurement_count", "shots"):
		lines.append(
			f"      {key}: {_yaml_scalar(_profile_value(baseline, key))}")
	lines.extend([
		"    timing:",
		"      one_q_gate_ns: " +
		_yaml_scalar(_profile_value(profile, "one_q_gate_ns")),
		"      two_q_gate_ns: " +
		_yaml_scalar(_profile_value(profile, "two_q_gate_ns")),
		"      measurement_ns: " +
		_yaml_scalar(_profile_value(profile, "measurement_ns")),
		"      one_q_gate_transfer_ns: " +
		_yaml_scalar(_profile_value(profile, "one_q_gate_transfer_ns")),
		"      two_q_gate_transfer_ns: " +
		_yaml_scalar(_profile_value(profile, "two_q_gate_transfer_ns")),
		"      measurement_transfer_ns: " +
		_yaml_scalar(_profile_value(profile, "measurement_transfer_ns")),
		f"      compile_ns: {_yaml_scalar(_profile_value(profile, 'compile_ns'))}",
		"      control_overhead_ns: " +
		_yaml_scalar(_profile_value(profile, "control_overhead_ns")),
		"      provider_overhead_ns: " +
		_yaml_scalar(_profile_value(profile, "provider_overhead_ns")),
		"    credit:",
		f"      total_credits: {_yaml_scalar(_profile_value(profile, 'total_credits'))}",
		"    rate:",
		f"      device_rate: {_yaml_scalar(_profile_value(profile, 'device_rate'))}",
		"      concurrent_jobs: " +
		_yaml_scalar(_profile_value(profile, "concurrent_jobs")),
	])
	_append_estimator_config(lines, estimator)
	_append_policy_config(lines, policy)
	return "\n".join(lines) + "\n"


def _append_estimator_config(lines, estimator):
	estimator_name = _estimator_name(estimator)
	if not estimator_name:
		return
	lines.extend([
		"    estimator:",
		f"      name: {_yaml_scalar(estimator_name)}",
	])
	_append_option_config(lines, _estimator_options(estimator), "      ")


def _append_policy_config(lines, policy):
	policy_name = _policy_name(policy)
	if not policy_name:
		return
	lines.extend([
		"    policy:",
		f"      name: {_yaml_scalar(policy_name)}",
	])
	_append_option_config(lines, _policy_options(policy), "      ")


def _append_option_config(lines, options, indent):
	if not options:
		return
	lines.append(indent + "options:")
	for key in sorted(options, key=str):
		lines.append(
			f"{indent}  {_yaml_key(key)}: {_yaml_scalar(options[key])}")


def _policy_name(policy):
	policy = dict(policy or {})
	return (
		policy.get("name") or
		policy.get("policy") or
		policy.get("policy_name"))


def _estimator_name(estimator):
	estimator = dict(estimator or {})
	return (
		estimator.get("name") or
		estimator.get("estimator") or
		estimator.get("estimator_name"))


def _device_profile_dict(profile):
	if profile is None:
		return None
	if isinstance(profile, dict):
		return dict(profile)
	baseline = getattr(profile, "baseline", None)
	return {
		"device_id": getattr(profile, "device_id", None),
		"time_span_ns": getattr(profile, "time_span_ns", 0),
		"baseline": _baseline_dict(baseline),
		"max_qubits": getattr(profile, "max_qubits", 0),
		"max_shots": getattr(profile, "max_shots", 0),
		"max_provider_queue_depth": getattr(
			profile, "max_provider_queue_depth", 0),
		"one_q_gate_ns": getattr(profile, "one_q_gate_ns", 0),
		"two_q_gate_ns": getattr(profile, "two_q_gate_ns", 0),
		"measurement_ns": getattr(profile, "measurement_ns", 0),
		"one_q_gate_transfer_ns": getattr(
			profile, "one_q_gate_transfer_ns", 0),
		"two_q_gate_transfer_ns": getattr(
			profile, "two_q_gate_transfer_ns", 0),
		"measurement_transfer_ns": getattr(
			profile, "measurement_transfer_ns", 0),
		"compile_ns": getattr(profile, "compile_ns", 0),
		"control_overhead_ns": getattr(profile, "control_overhead_ns", 0),
		"provider_overhead_ns": getattr(profile, "provider_overhead_ns", 0),
		"total_credits": getattr(profile, "total_credits", 0),
		"device_rate": getattr(profile, "device_rate", 0),
		"concurrent_jobs": getattr(profile, "concurrent_jobs", 0),
		"default_ttl_ns": getattr(profile, "default_ttl_ns", 0),
	}


def _baseline_dict(baseline):
	if baseline is None:
		return {}
	if isinstance(baseline, dict):
		return dict(baseline)
	return {
		"qubit_count": getattr(baseline, "qubit_count", 0),
		"depth": getattr(baseline, "depth", 0),
		"one_q_gate_count": getattr(baseline, "one_q_gate_count", 0),
		"two_q_gate_count": getattr(baseline, "two_q_gate_count", 0),
		"measurement_count": getattr(baseline, "measurement_count", 0),
		"shots": getattr(baseline, "shots", 0),
	}


def _profile_value(profile, key, default=0):
	if isinstance(profile, dict):
		return profile.get(key, default)
	return getattr(profile, key, default)


def _yaml_key(key):
	return str(key).replace(" ", "_")


def _yaml_scalar(value):
	if value is None:
		value = 0
	if isinstance(value, bool):
		return "true" if value else "false"
	text = str(value)
	if "\n" in text or "\r" in text:
		raise QPMAdmissionValidationError(
			"admission policy options cannot contain newlines")
	return text


def _native_admission_request(qhw_admission, request):
	task_info = dict(request.get("task_class", {}))
	task_class = qhw_admission.QtaskClass(
		class_id=task_info.get("class_id", 1),
		count=task_info.get("count", 1),
		qubit_count=task_info.get("qubit_count", 1),
		depth=task_info.get("depth", 1),
		one_q_gate_count=task_info.get("one_q_gate_count", 0),
		two_q_gate_count=task_info.get("two_q_gate_count", 0),
		shots=task_info.get("shots", 1),
		measurement_count=task_info.get("measurement_count", 1),
	)
	workload_kind = _workload_kind(qhw_admission, request.get("workload_kind"))
	return qhw_admission.AdmissionRequest(
		request_id=request["request_id"],
		device_id=request["device_id"],
		user_id=request["user_id"],
		job_id=request["job_id"],
		scope_id=request["scope_id"],
		workload_kind=workload_kind,
		walltime_ns=request.get("walltime_ns", 0),
		task_class=task_class,
		reservation_id=request.get("reservation_id", 0),
		ttl_ns=request.get("ttl_ns", 0),
		classical_runtime_ns=request.get("classical_runtime_ns", 0),
		overhead_ns=request.get("overhead_ns", 0),
		priority=request.get("priority", 0),
	)


def _native_usage(qhw_admission, reservation_id, usage):
	return qhw_admission.Usage(
		reservation_id=reservation_id,
		task_id=usage.get("task_id", 0),
		class_id=usage.get("class_id", 0),
		event_time_ns=usage.get("event_time_ns", 0),
		estimated_ns=usage.get("estimated_ns", 0),
		actual_ns=usage.get("actual_ns", 0),
		baseline_units=usage.get("baseline_units", 0),
		credits=usage.get("credits", 0),
		rate_units=usage.get("rate_units", 0),
	)


def _workload_kind(qhw_admission, workload_kind):
	if isinstance(workload_kind, int):
		return workload_kind
	if workload_kind == "hybrid":
		return qhw_admission.WORKLOAD_HYBRID_JOB
	return qhw_admission.WORKLOAD_QUANTUM_JOB


def _decision_dict(decision):
	if isinstance(decision, dict):
		return dict(decision)
	status = _decision_status(getattr(decision, "decision", None))
	return {
		"status": status,
		"decision": getattr(decision, "decision", None),
		"request_id": getattr(decision, "request_id", None),
		"device_id": getattr(decision, "device_id", None),
		"scope_id": getattr(decision, "scope_id", None),
		"reservation_id": getattr(decision, "reservation_id", None),
		"reason": _reason_label(getattr(decision, "reason_code", None)),
		"reason_code": getattr(decision, "reason_code", None),
		"credits_required": getattr(decision, "credits_required", None),
		"rate_required": getattr(decision, "rate_required", None),
		"capacity_available": getattr(decision, "capacity_available", None),
		"estimated_total_ns": getattr(decision, "estimated_total_ns", None),
		"estimated_start_ns": getattr(decision, "estimated_start_ns", None),
		"estimated_finish_ns": getattr(decision, "estimated_finish_ns", None),
		"retry_after_ns": getattr(decision, "retry_after_ns", None),
		"message": getattr(decision, "message", None),
	}


def _reservation_dict(reservation):
	if isinstance(reservation, dict):
		return dict(reservation)
	return {
		"reservation_id": getattr(reservation, "reservation_id", None),
		"request_id": getattr(reservation, "request_id", None),
		"device_id": getattr(reservation, "device_id", None),
		"scope_id": getattr(reservation, "scope_id", None),
		"user_id": getattr(reservation, "user_id", None),
		"job_id": getattr(reservation, "job_id", None),
		"workload_kind": getattr(reservation, "workload_kind", None),
		"state": _reservation_state(getattr(reservation, "state", None)),
		"state_code": getattr(reservation, "state", None),
		"credits_reserved": getattr(reservation, "credits_reserved", None),
		"credits_consumed": getattr(reservation, "credits_consumed", None),
		"rate_reserved": getattr(reservation, "rate_reserved", None),
		"rate_consumed": getattr(reservation, "rate_consumed", None),
		"quantum_budget_ns": getattr(reservation, "quantum_budget_ns", None),
		"estimated_total_ns": getattr(reservation, "estimated_total_ns", None),
		"actual_total_ns": getattr(reservation, "actual_total_ns", None),
		"created_at_ns": getattr(reservation, "created_at_ns", None),
		"expires_at_ns": getattr(reservation, "expires_at_ns", None),
	}


def _decision_status(decision):
	if decision == 1:
		return DECISION_ACCEPTED
	if decision == 2:
		return DECISION_DELAYED
	if decision == 3:
		return DECISION_REJECTED
	return "unknown"


def _reservation_state(state):
	if state == 1:
		return "pending"
	if state == 2:
		return "active"
	if state == 3:
		return "released"
	if state == 4:
		return "expired"
	if state == 5:
		return "cancelled"
	return "unknown"


def _reason_label(reason_code):
	if reason_code in (None, 0, 1):
		return "accepted"
	return f"reason-{reason_code}"
