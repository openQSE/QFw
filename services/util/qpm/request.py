import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


AUTH_DISABLED_ENV = "QFW_QPM_AUTH_DISABLED"

REQUEST_CONTEXT_KEYS = (
	"reservation_id",
	"token",
	"timeout",
	"cancel_on_timeout",
	"owner",
	"job_id",
	"allocation_id",
	"project_id",
	"session_id",
	"target_device_id",
	"scope_id",
	"workload",
	"policy",
	"run_context",
)


@dataclass(frozen=True)
class QPMRequestContext:
	reservation_id: Optional[str] = None
	token: Any = None
	timeout: Any = None
	cancel_on_timeout: bool = False
	owner: Dict[str, Any] = field(default_factory=dict)
	job_id: Any = None
	allocation_id: Any = None
	project_id: Any = None
	session_id: Any = None
	target_device_id: Any = None
	scope_id: Any = None
	workload: Dict[str, Any] = field(default_factory=dict)
	policy: Dict[str, Any] = field(default_factory=dict)
	run_context: Dict[str, Any] = field(default_factory=dict)
	auth_disabled: bool = True

	def as_payload_fields(self):
		fields = {}
		for key in REQUEST_CONTEXT_KEYS:
			if key == "cancel_on_timeout":
				continue
			value = getattr(self, key)
			if value is None:
				continue
			if isinstance(value, dict) and not value:
				continue
			fields[key] = value
		if self.cancel_on_timeout:
			fields["cancel_on_timeout"] = True
		return fields


@dataclass(frozen=True)
class QPMExecutionRequest:
	payload: Dict[str, Any]
	context: QPMRequestContext


def auth_disabled():
	value = os.environ.get(AUTH_DISABLED_ENV, "yes").strip().lower()
	return value in ("1", "true", "yes", "on", "y")


def parse_execution_request(info, **overrides):
	payload = dict(info or {})
	context = _context_from_payload(payload, overrides)
	payload.update(context.as_payload_fields())
	return QPMExecutionRequest(payload=payload, context=context)


def status_envelope(status, *, reason=None, message=None, data=None,
		    reservation_id=None, qtask_id=None):
	envelope = {
		"status": status,
		"reason": reason,
		"message": message,
		"reservation_id": reservation_id,
		"qtask_id": qtask_id,
		"data": data or {},
	}
	return {key: value for key, value in envelope.items()
		if value not in (None, {})}


def _context_from_payload(payload, overrides):
	values = {}
	for key in REQUEST_CONTEXT_KEYS:
		value = overrides.get(key, None)
		if value is None:
			value = payload.get(key, None)
		values[key] = value
	values["owner"] = _dict_value(values["owner"])
	values["workload"] = _dict_value(values["workload"])
	values["policy"] = _dict_value(values["policy"])
	values["run_context"] = _dict_value(values["run_context"])
	values["cancel_on_timeout"] = bool(values["cancel_on_timeout"])
	values["auth_disabled"] = auth_disabled()
	return QPMRequestContext(**values)


def _dict_value(value):
	if value is None:
		return {}
	return dict(value)
