import logging
import os

import defw
from defw_app_util import defw_get_directory_service, SYSTEM_UP_TIMEOUT
from .qpm_resolver import (
	DIRECT_ENDPOINT_ENABLED_ENV,
	DIRECT_QPM_ENDPOINT_ENV,
	QPM_IMPL_ENV,
	QPMResolver,
	SITE_DIRSVC_ENDPOINTS_ENV,
)
from .reservation_set import (
	parse_qfw_reservations,
	select_qpm_reservation,
)


def _connect_qpm(dirsvc, qpm_type, qpm_capabilities,
		 timeout=SYSTEM_UP_TIMEOUT, provider=None, service_id=None):
	want = provider or os.environ.get(QPM_IMPL_ENV)
	resolver = QPMResolver.from_environment(dirsvc=dirsvc, defw_module=defw)
	reservations = parse_qfw_reservations(required=False)
	if reservations:
		selected = select_qpm_reservation(
			reservations, service_id=service_id)
		binding = resolver.connect_reserved(
			selected.service_id,
			selected.reservation_id,
			timeout=timeout,
			binding_name="execution")
		return binding.client, binding.reservation_id
	request = {
		"timeout": timeout,
		"binding_name": "execution",
		"qpm_type": qpm_type,
		"qpm_capabilities": qpm_capabilities,
	}
	if want:
		request["provider"] = want
	return resolver.connect(**request), None


def _external_qpm_resolution_configured():
	if os.environ.get(SITE_DIRSVC_ENDPOINTS_ENV):
		return True
	value = os.environ.get(DIRECT_ENDPOINT_ENABLED_ENV, "")
	if value.strip().lower() not in {"1", "true", "yes", "on", "y"}:
		return False
	return bool(os.environ.get(DIRECT_QPM_ENDPOINT_ENV))


def _embedded_directory_expected():
	value = os.environ.get("DEFW_DISABLE_DIRSVC", "")
	if value.strip().lower() in {"0", "false", "no", "off", "n"}:
		return True
	return bool(
		os.environ.get("DEFW_PARENT_HOSTNAME") and
		os.environ.get("DEFW_PARENT_PORT"))


def _optional_directory_service(timeout=SYSTEM_UP_TIMEOUT):
	try:
		return defw_get_directory_service(timeout=timeout)
	except Exception:
		if _embedded_directory_expected():
			raise
		if _external_qpm_resolution_configured():
			return None
		raise


def get_qpm(qpm_type=-1, qpm_capabilities=-1, timeout=SYSTEM_UP_TIMEOUT,
	    provider=None, service_id=None, return_reservation=False):
	# Grab a qpm if one exists.
	dirsvc = _optional_directory_service(timeout=timeout)
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
