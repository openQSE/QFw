import inspect


def test_qpm_compatibility_surface_keeps_existing_methods():
	from api_qpm import QPM

	for method_name in (
		"sync_run",
		"async_run",
		"read_cq",
		"peek_cq",
		"register_event_notification",
		"cancel_task",
		"task_status",
		"get_backend_info",
		"get_device_info",
		"shutdown",
	):
		assert hasattr(QPM, method_name)


def test_qpm_category_surfaces_are_importable():
	from api_qpm import (
		QPM,
		QPMAdmissionControl,
		QPMAdmissionPolicyConfig,
		QPMExecution,
		QPMSchedulerControl,
		QPMTelemetry,
	)

	assert issubclass(QPM, QPMExecution)
	assert issubclass(QPM, QPMAdmissionControl)
	assert issubclass(QPM, QPMAdmissionPolicyConfig)
	assert issubclass(QPM, QPMSchedulerControl)
	assert issubclass(QPM, QPMTelemetry)
	assert hasattr(QPMAdmissionControl, "reserve")
	assert hasattr(QPMAdmissionPolicyConfig, "configure_device_profile")
	assert hasattr(QPMAdmissionPolicyConfig, "configure_admission_policy")
	assert hasattr(QPMAdmissionPolicyConfig, "set_admission_policy")
	assert hasattr(QPMSchedulerControl, "configure_scheduler_policy")
	assert hasattr(QPMSchedulerControl, "pause_execution_target")
	assert hasattr(QPMSchedulerControl, "set_scheduler_policy")
	assert hasattr(QPMTelemetry, "get_calibration_snapshot")
	assert hasattr(QPMTelemetry, "reconcile_runtime_state")
	assert hasattr(QPMTelemetry, "get_service_lifecycle_telemetry")


def test_qpm_category_service_api_packages_export_single_surface():
	import api_qpm_admission_control
	import api_qpm_admission_policy_config
	import api_qpm_execution
	import api_qpm_scheduler_control
	import api_qpm_telemetry

	assert api_qpm_execution.svc_info["category"] == "execution"
	assert api_qpm_execution.service_classes[0].__name__ == "QPMExecution"
	assert (
		api_qpm_admission_control.svc_info["category"] == "admission"
	)
	assert (
		api_qpm_admission_policy_config.svc_info["category"] ==
		"admission-policy"
	)
	assert api_qpm_scheduler_control.svc_info["category"] == "scheduler"
	assert api_qpm_telemetry.svc_info["category"] == "telemetry"


def test_qpm_category_surfaces_accept_token_placeholders():
	from api_qpm import (
		QPMAdmissionControl,
		QPMAdmissionPolicyConfig,
		QPMExecution,
		QPMSchedulerControl,
		QPMTelemetry,
	)

	for cls, method_name in (
		(QPMExecution, "sync_run"),
		(QPMExecution, "async_run"),
		(QPMExecution, "diagnostic_sync_run"),
		(QPMExecution, "diagnostic_async_run"),
		(QPMExecution, "cancel_task"),
		(QPMExecution, "task_status"),
		(QPMAdmissionControl, "reserve"),
		(QPMAdmissionControl, "release"),
		(QPMAdmissionPolicyConfig, "configure_device_profile"),
		(QPMSchedulerControl, "configure_scheduler_policy"),
		(QPMTelemetry, "get_backend_info"),
		(QPMTelemetry, "get_task_metadata"),
	):
		signature = inspect.signature(getattr(cls, method_name))
		assert "token" in signature.parameters


def test_qpm_category_surfaces_use_token_first_order():
	from api_qpm import (
		QPMAdmissionPolicyConfig,
		QPMExecution,
		QPMSchedulerControl,
		QPMTelemetry,
	)

	expected = {
		(QPMExecution, "cancel_task"):
			["self", "cid", "reservation_id", "token",
			 "reason", "qtask_id"],
		(QPMExecution, "task_status"):
			["self", "cid", "reservation_id", "token", "qtask_id"],
		(QPMExecution, "diagnostic_sync_run"):
			["self", "info", "token", "reason"],
		(QPMExecution, "diagnostic_async_run"):
			["self", "info", "token", "reason"],
		(QPMAdmissionPolicyConfig, "set_capacity_model"):
			["self", "token", "device_id", "capacity_model"],
		(QPMSchedulerControl, "set_dispatch_depth"):
			["self", "token", "device_id", "max_inflight"],
		(QPMSchedulerControl, "get_scheduler_queue_state"):
			["self", "token", "device_id", "include_restricted"],
		(QPMTelemetry, "get_task_metadata"):
			["self", "token", "cid", "reservation_id", "qtask_id"],
	}

	for (cls, method_name), parameters in expected.items():
		signature = inspect.signature(getattr(cls, method_name))
		assert list(signature.parameters) == parameters


def test_util_qpm_control_methods_accept_token_first_order():
	from util.qpm.util_qpm import UTIL_QPM

	class RecordingController:
		def __init__(self):
			self.capacity_model = None
			self.dispatch_depth = None
			self.include_restricted = None

		def set_capacity_model(self, capacity_model, device_id=None):
			self.device_id = device_id
			self.capacity_model = capacity_model
			return {"capacity_model": capacity_model}

		def set_dispatch_depth(self, max_inflight):
			self.dispatch_depth = max_inflight
			return {"max_inflight": max_inflight}

		def get_scheduler_queue_state(self, include_restricted=False):
			self.include_restricted = include_restricted
			return {"include_restricted": include_restricted}

	qpm = UTIL_QPM.__new__(UTIL_QPM)
	qpm.controller = RecordingController()

	assert qpm.set_capacity_model(
		"opaque-token", "device-1", {"credits": 5}) == {
			"capacity_model": {"credits": 5},
		}
	assert qpm.set_dispatch_depth("opaque-token", "device-1", 4) == {
		"max_inflight": 4,
	}
	assert qpm.set_dispatch_depth({"opaque": "token"}, "device-1", 5) == {
		"max_inflight": 5,
	}
	assert qpm.get_scheduler_queue_state(
		"opaque-token", "device-1", True) == {
			"include_restricted": True,
		}
	assert qpm.get_scheduler_queue_state(
		{"opaque": "token"}, "device-1", False) == {
			"include_restricted": False,
		}


def test_util_qpm_execution_methods_accept_phase3_positional_order():
	from util.qpm.util_qpm import UTIL_QPM

	class RecordingController:
		def __init__(self):
			self.validated = []
			self.cancel_kwargs = None
			self.cid_status_kwargs = None
			self.qtask_status_kwargs = None

		def validate_reservation_for_context(self, context):
			self.validated.append(context)

		def cancel_task(self, **kwargs):
			self.cancel_kwargs = kwargs
			return {"outcome": "CANCELLED"}

		def task_status_for_cid(self, cid, reservation_id=None,
					require_reservation=False):
			self.cid_status_kwargs = {
				"cid": cid,
				"reservation_id": reservation_id,
				"require_reservation": require_reservation,
			}
			return {"cid": cid}

		def task_status_for_qtask_id(self, qtask_id, reservation_id=None,
					     require_reservation=False):
			self.qtask_status_kwargs = {
				"qtask_id": qtask_id,
				"reservation_id": reservation_id,
				"require_reservation": require_reservation,
			}
			return {"qtask_id": qtask_id}

	qpm = UTIL_QPM.__new__(UTIL_QPM)
	qpm.controller = RecordingController()
	qpm.process_oor_queue = lambda: None

	assert qpm.task_status(
		"cid-1", "reservation-1", "opaque-token") == {
			"cid": "cid-1",
		}
	assert qpm.controller.cid_status_kwargs == {
		"cid": "cid-1",
		"reservation_id": "reservation-1",
		"require_reservation": True,
	}

	assert qpm.cancel_task(
		"cid-1", "reservation-1", "opaque-token", "stop") == {
			"outcome": "CANCELLED",
		}
	assert qpm.controller.validated[-1].reservation_id == "reservation-1"
	assert qpm.controller.validated[-1].token == "opaque-token"
	assert qpm.controller.cancel_kwargs == {
		"cid": "cid-1",
		"qtask_id": None,
		"reason": "stop",
		"reservation_id": "reservation-1",
		"require_reservation": True,
	}

	assert qpm.task_status(
		qtask_id=7, reservation_id="reservation-2",
		token={"opaque": "token"}) == {
			"qtask_id": 7,
		}
	assert qpm.controller.qtask_status_kwargs == {
		"qtask_id": 7,
		"reservation_id": "reservation-2",
		"require_reservation": True,
	}

	assert qpm.cancel_task(
		qtask_id=7, reservation_id="reservation-2",
		token={"opaque": "token"}, reason="keyword-stop") == {
			"outcome": "CANCELLED",
		}
	assert qpm.controller.validated[-1].reservation_id == "reservation-2"
	assert qpm.controller.validated[-1].token == {"opaque": "token"}
	assert qpm.controller.cancel_kwargs == {
		"cid": None,
		"qtask_id": 7,
		"reason": "keyword-stop",
		"reservation_id": "reservation-2",
		"require_reservation": True,
	}


def test_util_qpm_list_reservations_preserves_dict_tokens():
	from util.qpm.util_qpm import UTIL_QPM

	class RecordingController:
		def __init__(self):
			self.calls = []

		def list_admission_reservations(self, filters=None, token=None):
			call = {"filters": filters, "token": token}
			self.calls.append(call)
			return [call]

	qpm = UTIL_QPM.__new__(UTIL_QPM)
	qpm.controller = RecordingController()
	token = {"opaque": "token"}
	filters = {"owner": "alice"}

	assert qpm.list_reservations(token) == [{
		"filters": None,
		"token": token,
	}]
	assert qpm.controller.calls[-1] == {
		"filters": None,
		"token": token,
	}

	assert qpm.list_reservations(filters=filters) == [{
		"filters": filters,
		"token": None,
	}]
	assert qpm.controller.calls[-1] == {
		"filters": filters,
		"token": None,
	}
