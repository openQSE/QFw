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


def set_policy(context, device_id, policy):
	if not admission_context_available(context):
		return None
	handler = getattr(context, "set_admission_policy", None)
	if handler is not None:
		return handler(device_id, dict(policy))

	policy_path = policy.get("path")
	if policy_path:
		context.add_policy_path(policy_path)
	policy_name = policy.get("name") or policy.get("policy")
	if policy_name:
		return context.set_policy(device_id, policy_name)
	return None


def set_estimator(context, device_id, estimator):
	if not admission_context_available(context):
		return None
	handler = getattr(context, "set_estimator_policy", None)
	if handler is not None:
		return handler(device_id, dict(estimator))

	estimator_path = estimator.get("path")
	if estimator_path:
		context.add_estimator_path(estimator_path)
	estimator_name = estimator.get("name") or estimator.get("estimator")
	if estimator_name:
		return context.set_estimator(device_id, estimator_name)
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
