import logging
import os

import defw
from defw_app_util import defw_get_directory_service, SYSTEM_UP_TIMEOUT
from .qpm_resolver import (
	QPM_IMPL_ENV,
	QPMResolver,
)
from .reservation_set import (
	parse_qfw_reservations,
	select_qpm_reservation,
)

# The API bindings a QPM publishes that the Qiskit client binds: execution
# for the circuits, telemetry for the device (api_qpm_telemetry).
EXECUTION_BINDING = "execution"
TELEMETRY_BINDING = "telemetry"


def _with_declared_properties(client, resolved):
	# Keep the QPM's declared directory properties on the client, so a job can
	# see what the service said about itself. The circuit formats it reads are
	# the ones that matter here: QFwJob asks for them before it serializes.
	# A client object that will not take the attribute is left alone, since a
	# missing declaration only means the defaults.
	try:
		setattr(client, "qpm_properties", dict(resolved.properties or {}))
	except Exception:
		logging.debug("could not record QPM properties on the client")
	return client


def _connect_qpm(dirsvc, qpm_type, qpm_capabilities,
		 timeout=SYSTEM_UP_TIMEOUT, provider=None, service_id=None):
	resolved, lifecycle_binding, reservation_id = bind_qpm(
		dirsvc, qpm_type, qpm_capabilities, timeout=timeout,
		provider=provider, service_id=service_id)
	client = lifecycle_binding.api(
		resolved.api_binding.binding_name,
		expected_runtime_id=resolved.runtime_id,
	)
	return _with_declared_properties(client, resolved), reservation_id


def bind_qpm(dirsvc, qpm_type=-1, qpm_capabilities=-1,
	     timeout=SYSTEM_UP_TIMEOUT, provider=None, service_id=None):
	want = provider or os.environ.get(QPM_IMPL_ENV)
	resolver = QPMResolver.from_environment(dirsvc=dirsvc, defw_module=defw)
	reservations = parse_qfw_reservations(required=False)
	if reservations:
		selected = select_qpm_reservation(
			reservations, service_id=service_id)
		binding = resolver.connect_reserved_managed(
			selected.service_id,
			selected.reservation_id,
			timeout=timeout,
			binding_name=EXECUTION_BINDING)
		return (binding.resolved, binding.lifecycle_binding,
			binding.reservation_id)
	request = {
		"timeout": timeout,
		"binding_name": EXECUTION_BINDING,
		"qpm_type": qpm_type,
		"qpm_capabilities": qpm_capabilities,
	}
	if want:
		request["provider"] = want
	if service_id:
		request["service_id"] = service_id
	resolved = resolver.resolve(**request)
	return resolved, resolver.managed_binding(resolved), None


def get_qpm(qpm_type=-1, qpm_capabilities=-1, timeout=SYSTEM_UP_TIMEOUT,
	    provider=None, service_id=None, return_reservation=False):
	# Grab a qpm if one exists.
	dirsvc = defw_get_directory_service(timeout=timeout)
	qpm_api, reservation_id = _connect_qpm(
		dirsvc,
		qpm_type,
		qpm_capabilities,
		timeout=timeout,
		provider=provider,
		service_id=service_id)

	logging.debug(f"got the qpm {qpm_api}")
	if return_reservation:
		return qpm_api, reservation_id
	return qpm_api


def get_qpm_telemetry(qpm_api):
	# The telemetry API of the QPM behind an execution client, bound next to
	# it. get_qpm() returns a managed binding: its lifecycle binding holds
	# the directory's service record, which lists every API the QPM
	# publishes, so the telemetry API is selected from that same record and
	# follows the same runtime, reconnecting with it. A client that is not a
	# managed binding (a bare proxy, a test fake) has no telemetry: None.
	lifecycle_binding = getattr(qpm_api, "lifecycle_binding", None)
	api = getattr(lifecycle_binding, "api", None)
	if not callable(api):
		return None
	return api(
		TELEMETRY_BINDING,
		expected_runtime_id=getattr(qpm_api, "expected_runtime_id", None))
