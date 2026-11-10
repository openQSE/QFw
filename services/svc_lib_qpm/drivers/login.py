# The login a driver opens a provider resource with, shared by QRMI and QDMI.

from defw_exception import DEFwExecutionError
import os


def resolve_login(descriptor, credential, provider, needs_lookup, failure,
		extras=()):
	# In order: the reservation's credential, QFW_QC_URL/QFW_API_KEY, then
	# the user's device-access entry, looked up only when needs_lookup says
	# the first two left something out. extras are keys taken from the
	# credential, else from the entry.
	credential = dict(credential or {})
	login = {
		"device_id": credential.get("device_id") or descriptor.get("id"),
		"base_url": credential.get("url") or os.environ.get("QFW_QC_URL"),
		"token": (
			credential.get("api_key")
			or credential.get("token")
			or os.environ.get("QFW_API_KEY")),
		"provider_device_id": (
			credential.get("provider_device_id")
			or credential.get("quantum_computer")
			or descriptor.get("provider_device_id")
			or descriptor.get("provider-device-id")),
	}
	login.update({key: credential.get(key) for key in extras})
	if not needs_lookup(login):
		return login
	try:
		from util.device_access import resolve_device_access
		entry = resolve_device_access(
			provider=provider,
			device_id=login["device_id"],
			user=credential.get("user"),
			credential_hint=credential.get("credential_hint"),
			credential_handle=credential.get("credential_handle"))
	except Exception as exc:
		raise DEFwExecutionError(f"{failure}: {exc}") from exc
	login["base_url"] = login["base_url"] or entry.get("url")
	login["token"] = login["token"] or entry.get("api_key")
	login["device_id"] = login["device_id"] or entry.get("device_id")
	login["provider_device_id"] = (
		login["provider_device_id"]
		or entry.get("provider_device_id")
		or entry.get("quantum_computer"))
	for key in extras:
		login[key] = login[key] or entry.get(key)
	return login
