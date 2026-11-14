from dataclasses import dataclass

from defw_exception import DEFwExecutionError

DEFAULT_MAX_MEMORY_MB = 4096
SETTINGS = ("max-memory-mb",)


@dataclass(frozen=True)
class ProxySettings:
	max_memory_mb: int = DEFAULT_MAX_MEMORY_MB


def _positive_mib(key, value):
	try:
		number = int(str(value).strip())
	except ValueError:
		number = 0
	if number <= 0:
		raise DEFwExecutionError(
			f"proxy {key} must be a positive whole number of MiB, not {value!r}")
	return number


def proxy_settings_from_service_entry(entry):
	block = entry.get("proxy") or {}
	if not isinstance(block, dict):
		raise DEFwExecutionError(
			f"the proxy block holds settings by name, not {block!r}")
	unknown = set(block) - set(SETTINGS)
	if unknown:
		raise DEFwExecutionError(
			f"unknown proxy settings: {', '.join(sorted(unknown))}. The "
			f"proxy reads {', '.join(SETTINGS)}")
	return ProxySettings(max_memory_mb=_positive_mib(
		"max-memory-mb", block.get("max-memory-mb", DEFAULT_MAX_MEMORY_MB)))
