import inspect


QHW_ADM_THREAD_SAFE = "QHW_ADM_THREAD_SAFE"
QHW_ADM_THREAD_USER = "QHW_ADM_THREAD_USER"
DEFAULT_WORKLOAD_KIND = "QHW_ADM_WORKLOAD_QUANTUM_JOB"


class QPMAdmissionUnavailable(RuntimeError):
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
