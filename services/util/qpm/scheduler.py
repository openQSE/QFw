QHW_SCHED_THREAD_SAFE = "QHW_SCHED_THREAD_SAFE"
QHW_SCHED_THREAD_USER = "QHW_SCHED_THREAD_USER"

DEFAULT_SCHEDULER_POLICY = "fifo"
STANDARD_SCHEDULER_POLICIES = frozenset(
	("fifo", "ordered", "priority", "round_robin"))


class QPMSchedulerError(RuntimeError):
	pass


class QPMSchedulerUnavailable(QPMSchedulerError):
	pass


class QPMSchedulerQueueEmpty(QPMSchedulerError):
	pass


class UnavailableSchedulerContext:
	available = False

	def __init__(self, error):
		self.error = str(error)


class NativeSchedulerContext:
	available = True

	def __init__(self, qhw_scheduler, qpu, scheduler, threading_mode):
		self.qhw_scheduler = qhw_scheduler
		self.qpu = qpu
		self.scheduler = scheduler
		self.threading = threading_mode
		self.loaded_standard_plugins = set()

	def close(self):
		self.scheduler.close()
		self.qpu.close()


def create_scheduler_context(threading_mode=QHW_SCHED_THREAD_SAFE,
			     target_id=None, qpu_id=None, num_qubits=1,
			     policy_name=DEFAULT_SCHEDULER_POLICY):
	try:
		import qhw_scheduler
	except Exception as error:
		return UnavailableSchedulerContext(error)

	try:
		qpu = qhw_scheduler.QPU(
			qpu_id=qpu_id if qpu_id is not None else _target_numeric_id(
				target_id),
			num_qubits=max(1, int(num_qubits or 1)),
		)
		scheduler = qhw_scheduler.Scheduler(
			qpu,
			threading=_native_threading_value(qhw_scheduler, threading_mode),
		)
		context = NativeSchedulerContext(
			qhw_scheduler, qpu, scheduler, threading_mode)
		set_scheduler_policy(
			context, {"policy_name": policy_name, "options": {}})
		return context
	except Exception as error:
		return UnavailableSchedulerContext(error)


def scheduler_context_available(context):
	return bool(getattr(context, "available", False))


def require_scheduler_context(context):
	if scheduler_context_available(context):
		return context
	raise QPMSchedulerUnavailable(
		f"qhw-scheduler context is unavailable: {context.error}")


def set_scheduler_policy(context, policy):
	context = require_scheduler_context(context)
	normalized = normalize_scheduler_policy(policy)
	name = normalized["policy_name"]
	if hasattr(context, "set_policy"):
		context.set_policy(name, normalized["options"])
		return normalized
	options = _native_options(context.qhw_scheduler, normalized["options"])
	if name in STANDARD_SCHEDULER_POLICIES:
		_load_standard_scheduler_plugin(context, name)
	context.scheduler.set_policy(name, options=options)
	return normalized


def _load_standard_scheduler_plugin(context, name):
	loaded = getattr(context, "loaded_standard_plugins", None)
	if loaded is not None and name in loaded:
		return
	context.scheduler.load_standard_plugin(name)
	if loaded is not None:
		loaded.add(name)


def submit_scheduler_task(context, task):
	context = require_scheduler_context(context)
	if hasattr(context, "submit_task"):
		return context.submit_task(dict(task))
	context.scheduler.submit_task(
		task_id=task["task_id"],
		parent_task_id=task.get("parent_task_id", 0),
		owner_id=task.get("owner_id", 0),
		job_id=task.get("job_id", 0),
		reservation_id=task.get("reservation_id", 0),
		priority=task.get("priority", 0),
		deadline_ns=task.get("deadline_ns", 0),
		estimated_runtime_ns=task.get("estimated_runtime_ns", 0),
		estimated_cost=task.get("estimated_cost", 0),
		payload=task.get("payload"),
		metadata=_native_options(
			context.qhw_scheduler, task.get("metadata", {})),
	)
	return task["task_id"]


def select_next_scheduler_task(context):
	context = require_scheduler_context(context)
	if hasattr(context, "select_next_assignment"):
		assignment = context.select_next_assignment()
		if assignment is None:
			raise QPMSchedulerQueueEmpty("scheduler queue is empty")
		return dict(assignment) if isinstance(assignment, dict) else assignment
	try:
		assignment = context.scheduler.select_next_assignment()
	except Exception as error:
		if _is_empty_selection_error(context.qhw_scheduler, error):
			raise QPMSchedulerQueueEmpty(
				"scheduler queue is empty") from error
		raise
	return {
		"task_id": assignment.task_id,
		"parent_task_id": assignment.parent_task_id,
		"slice_index": assignment.slice_index,
		"slice_count": assignment.slice_count,
		"estimated_runtime_ns": assignment.estimated_runtime_ns,
		"estimated_cost": assignment.estimated_cost,
		"payload": assignment.payload_bytes,
	}


def mark_scheduler_task_started(context, scheduler_task_id):
	context = require_scheduler_context(context)
	if hasattr(context, "task_started"):
		context.task_started(scheduler_task_id)
		return
	context.scheduler.task_started(scheduler_task_id)


def mark_scheduler_task_completed(context, scheduler_task_id):
	context = require_scheduler_context(context)
	if hasattr(context, "task_completed"):
		context.task_completed(scheduler_task_id)
		return
	context.scheduler.task_completed(scheduler_task_id)


def mark_scheduler_task_failed(context, scheduler_task_id):
	context = require_scheduler_context(context)
	if hasattr(context, "task_failed"):
		context.task_failed(scheduler_task_id)
		return
	context.scheduler.task_failed(scheduler_task_id)


def mark_scheduler_task_cancelled(context, scheduler_task_id):
	context = require_scheduler_context(context)
	if hasattr(context, "task_cancelled"):
		context.task_cancelled(scheduler_task_id)
		return
	context.scheduler.task_cancelled(scheduler_task_id)


def scheduler_task_state(context, scheduler_task_id):
	context = require_scheduler_context(context)
	if hasattr(context, "task_state"):
		return context.task_state(scheduler_task_id)
	return context.scheduler.task_state(scheduler_task_id)


def scheduler_task_state_name(context, scheduler_task_id):
	state = scheduler_task_state(context, scheduler_task_id)
	if isinstance(state, str):
		return state
	qhw_scheduler = getattr(context, "qhw_scheduler", None)
	if qhw_scheduler is None:
		return state
	state_names = {
		getattr(qhw_scheduler, "QHW_SCHED_TASK_UNKNOWN", None): "unknown",
		getattr(qhw_scheduler, "QHW_SCHED_TASK_QUEUED", None): "queued",
		getattr(qhw_scheduler, "QHW_SCHED_TASK_RUNNING", None): "running",
		getattr(qhw_scheduler, "QHW_SCHED_TASK_COMPLETED", None): "completed",
		getattr(qhw_scheduler, "QHW_SCHED_TASK_FAILED", None): "failed",
		getattr(qhw_scheduler, "QHW_SCHED_TASK_CANCELLED", None): "cancelled",
		getattr(qhw_scheduler, "QHW_SCHED_TASK_ASSIGNED", None): "selected",
		getattr(qhw_scheduler, "QHW_SCHED_TASK_WAITING", None): "waiting",
	}
	return state_names.get(state, state)


def scheduler_task_count(context):
	if not scheduler_context_available(context):
		return None
	if hasattr(context, "task_count"):
		return context.task_count()
	return context.scheduler.task_count()


def normalize_scheduler_policy(policy):
	if policy is None:
		policy = {}
	if not isinstance(policy, dict):
		policy = {"policy_name": str(policy)}
	policy = dict(policy)
	name = (
		policy.get("policy_name") or
		policy.get("name") or
		policy.get("policy") or
		DEFAULT_SCHEDULER_POLICY)
	options = (
		policy.get("options") or
		policy.get("policy_options") or
		policy.get("scheduler_options") or
		{})
	return {
		"policy_name": str(name),
		"options": _plain_options(options),
	}


def _native_threading_value(qhw_scheduler, threading_mode):
	if threading_mode == QHW_SCHED_THREAD_SAFE:
		return qhw_scheduler.QHW_SCHED_THREAD_SAFE
	if threading_mode == QHW_SCHED_THREAD_USER:
		return qhw_scheduler.QHW_SCHED_THREAD_USER
	raise ValueError(f"unsupported qhw-scheduler threading mode: {threading_mode}")


def _native_options(qhw_scheduler, options):
	if not options:
		return []
	if isinstance(options, (list, tuple)):
		return list(options)
	items = []
	for key, value in _plain_options(options).items():
		native_key = int(key)
		if isinstance(value, bool):
			value = int(value)
		if isinstance(value, int):
			items.append(qhw_scheduler.kv_u64(native_key, value))
		elif isinstance(value, float):
			items.append(qhw_scheduler.kv_f64(native_key, value))
		else:
			raise QPMSchedulerError(
				f"unsupported scheduler option value: key={key}")
	return items


def _is_empty_selection_error(qhw_scheduler, error):
	return (
		getattr(error, "rc", None) ==
		getattr(qhw_scheduler, "QHW_SCHED_ERR_NOT_FOUND", object()))


def _plain_options(options):
	if not options:
		return {}
	if isinstance(options, dict):
		return dict(options)
	result = {}
	for item in options:
		if isinstance(item, dict) and "key" in item and "value" in item:
			result[item["key"]] = item["value"]
	return result


def _target_numeric_id(target_id):
	if target_id is None:
		return 1
	value = 1469598103934665603
	for byte in str(target_id).encode("utf-8"):
		value ^= byte
		value *= 1099511628211
		value &= 0xFFFFFFFFFFFFFFFF
	return value or 1
