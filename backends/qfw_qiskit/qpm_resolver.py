import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from time import sleep
from typing import Any, Dict, Optional

import defw
from defw_exception import DEFwReserveError


QPM_IMPL_ENV = "QFW_QPM_IMPL"
DEFAULT_QPM_IMPL = "iqm"
DEFAULT_SERVICE_NAME = "QPM"
DEFAULT_SERVICE_TYPE = "qfw.qpm"

API_CATEGORY_BINDINGS = {
	"execution": "execution",
	"admission": "admission",
	"admission-control": "admission",
	"admission-policy": "admission-policy",
	"scheduler": "scheduler",
	"scheduler-control": "scheduler",
	"telemetry": "telemetry",
	"discovery": "telemetry",
}


class QPMAmbiguousResolutionError(DEFwReserveError):
	pass


class QPMProviderPolicyError(DEFwReserveError):
	pass


@dataclass(frozen=True)
class DirectoryScope:
	name: str
	scope: str
	client: Any = None
	endpoint: Any = None
	identity: Optional[str] = None
	priority: int = 0
	enabled: bool = True


@dataclass(frozen=True)
class QPMApiBinding:
	binding_name: str
	client_module: str = "api_qpm"
	client_class: str = "QPM"
	service_module: Optional[str] = None
	service_class: str = "QPM"
	version: int = 1
	policy_labels: tuple = ()


@dataclass(frozen=True)
class QPMResolvedBinding:
	service_info: Any
	api_binding: QPMApiBinding
	directory_scope: str
	directory_identity: str
	endpoint: Any = None
	service_id: Optional[str] = None
	service_name: str = DEFAULT_SERVICE_NAME
	service_type: str = DEFAULT_SERVICE_TYPE
	runtime_id: Optional[str] = None
	generation: Optional[int] = None
	selector_metadata: Dict[str, Any] = field(default_factory=dict)
	properties: Dict[str, Any] = field(default_factory=dict)
	directory_priority: int = 0
	discovery_index: int = 0


@dataclass(frozen=True)
class QPMResolutionRequest:
	service_name: str = DEFAULT_SERVICE_NAME
	service_type: str = DEFAULT_SERVICE_TYPE
	api_category: str = "execution"
	binding_name: Optional[str] = None
	selector_resource: Optional[str] = None
	selector_alias: Optional[str] = None
	qpm_type: Any = -1
	qpm_capability: Any = -1
	provider: Optional[str] = None

	def binding_filter(self):
		if self.binding_name:
			return self.binding_name
		return API_CATEGORY_BINDINGS.get(self.api_category, self.api_category)


class DEFwQPMConnector:
	def __init__(self, defw_module=defw):
		self._defw = defw_module

	def connect(self, resolved):
		if hasattr(self._defw, "connect_to_binding"):
			return self._defw.connect_to_binding(resolved)
		if resolved.service_info is not None:
			apis = self._defw.connect_to_resource(
				[resolved.service_info],
				resolved.api_binding.client_class,
			)
			return apis[0]
		raise DEFwReserveError(
			f"resolved QPM {resolved.service_id!r} has no DEFw binding")


class QPMResolver:
	def __init__(self, directories, connector=None, sleeper=sleep):
		self._directories = list(directories)
		self._connector = connector or DEFwQPMConnector()
		self._sleep = sleeper

	@classmethod
	def from_resource_manager(cls, rmgr, defw_module=defw, sleeper=sleep):
		directory = DirectoryScope(
			name="allocation-local",
			scope="allocation-local",
			client=rmgr,
			identity="allocation-local",
			priority=100,
		)
		return cls([directory], DEFwQPMConnector(defw_module), sleeper)

	def connect(self, timeout=10, **kwargs):
		return self._connector.connect(self.resolve(timeout=timeout, **kwargs))

	def resolve(self, timeout=10, **kwargs):
		request = QPMResolutionRequest(**kwargs)
		candidates = []
		wait = 0
		while wait < timeout:
			candidates = self._collect_candidates(request)
			if candidates:
				break
			wait += 1
			logging.debug("Waiting to resolve QPM")
			self._sleep(1)
		if not candidates:
			raise DEFwReserveError(
				f"Couldn't connect to a QPM "
				f"({request.qpm_type}, {request.qpm_capability})")
		return self._select_candidate(candidates, request)

	def _collect_candidates(self, request):
		candidates = []
		for directory in self._directories:
			if not directory.enabled:
				continue
			records = self._query_directory(directory, request)
			for record in records:
				candidate = self._normalize_record(
					directory,
					record,
					request,
					len(candidates),
				)
				if self._matches_request(candidate, request):
					candidates.append(candidate)
		return candidates

	def _query_directory(self, directory, request):
		client = directory.client
		if client is None:
			return []
		if hasattr(client, "resolve_service"):
			return _as_list(client.resolve_service(
				service_name=request.service_name,
				service_type=request.service_type,
				binding_name=request.binding_filter(),
				selector_resource=request.selector_resource,
				selector_alias=request.selector_alias,
				api_category=request.api_category,
			))
		if hasattr(client, "get_services"):
			return _as_list(client.get_services(
				request.service_name,
				request.qpm_type,
				request.qpm_capability,
			))
		return []

	def _normalize_record(self, directory, record, request, discovery_index):
		if isinstance(record, QPMResolvedBinding):
			return record
		if isinstance(record, dict):
			return self._normalize_directory_record(
				directory,
				record,
				request,
				discovery_index,
			)
		return self._normalize_legacy_service_info(
			directory,
			record,
			request,
			discovery_index,
		)

	def _normalize_directory_record(self, directory, record, request,
					discovery_index):
		service = record.get("service_record", record)
		binding = record.get("selected_api_binding") or record.get("api_binding")
		api_binding = _api_binding_from_mapping(binding, request)
		properties = dict(service.get("properties") or {})
		selector = dict(service.get("selector") or {})
		endpoint = service.get("endpoint") or record.get("endpoint")
		service_id = service.get("service_id") or properties.get("service_id")
		return QPMResolvedBinding(
			service_info=record.get("service_info"),
			api_binding=api_binding,
			directory_scope=record.get("directory_scope", directory.scope),
			directory_identity=record.get(
				"directory_identity",
				directory.identity or directory.name,
			),
			endpoint=endpoint,
			service_id=service_id,
			service_name=service.get("service_name", request.service_name),
			service_type=service.get("service_type", request.service_type),
			runtime_id=service.get("runtime_id"),
			generation=service.get("generation"),
			selector_metadata=selector,
			properties=properties,
			directory_priority=directory.priority,
			discovery_index=discovery_index,
		)

	def _normalize_legacy_service_info(self, directory, service_info, request,
					   discovery_index):
		properties = _service_properties(service_info)
		endpoint = _call(service_info, "get_endpoint")
		service_module = _call(service_info, "get_module_name")
		service_id = (
			properties.get("service_id") or
			properties.get("id") or
			str(endpoint or discovery_index)
		)
		return QPMResolvedBinding(
			service_info=service_info,
			api_binding=QPMApiBinding(
				binding_name=request.binding_filter(),
				service_module=service_module,
			),
			directory_scope=directory.scope,
			directory_identity=directory.identity or directory.name,
			endpoint=endpoint,
			service_id=service_id,
			service_name=request.service_name,
			service_type=properties.get("service_type", request.service_type),
			runtime_id=properties.get("runtime_id"),
			generation=properties.get("generation"),
			selector_metadata=_selector_from_properties(properties),
			properties=properties,
			directory_priority=directory.priority,
			discovery_index=discovery_index,
		)

	def _matches_request(self, candidate, request):
		if request.service_type and candidate.service_type != request.service_type:
			return False
		if request.selector_resource:
			resources = candidate.selector_metadata.get("resources", [])
			if request.selector_resource not in resources:
				return False
		if request.selector_alias:
			aliases = candidate.selector_metadata.get("aliases", [])
			if request.selector_alias not in aliases:
				return False
		return candidate.api_binding.binding_name == request.binding_filter()

	def _select_candidate(self, candidates, request):
		ordered = sorted(
			candidates,
			key=lambda item: (-item.directory_priority, item.discovery_index),
		)
		if request.provider:
			matching_provider = [
				item for item in ordered
				if item.properties.get("provider") == request.provider
			]
			if not matching_provider:
				raise QPMProviderPolicyError(
					f"no QPM provider {request.provider!r} matched "
					f"{request.service_type}/{request.binding_filter()}: "
					f"{_candidate_list(ordered)}")
			tied = [
				item for item in matching_provider
				if item.directory_priority == (
					matching_provider[0].directory_priority)
			]
			if len(tied) > 1:
				raise QPMAmbiguousResolutionError(
					f"ambiguous QPM provider {request.provider!r} "
					f"resolution for "
					f"{request.service_type}/{request.binding_filter()}: "
					f"{_candidate_list(tied)}")
			logging.debug(
				f"selected QPM impl '{request.provider}' "
				f"({len(matching_provider)} of "
				f"{len(ordered)} match(es))")
			return matching_provider[0]
		return ordered[0]

def _api_binding_from_mapping(binding, request):
	if binding is None:
		return QPMApiBinding(binding_name=request.binding_filter())
	return QPMApiBinding(
		binding_name=binding.get("binding_name", request.binding_filter()),
		client_module=binding.get("client_module", "api_qpm"),
		client_class=binding.get("client_class", "QPM"),
		service_module=binding.get("service_module"),
		service_class=binding.get("service_class", "QPM"),
		version=binding.get("version", 1),
		policy_labels=tuple(binding.get("policy_labels", ())),
	)


def _as_list(value):
	if value is None:
		return []
	if isinstance(value, list):
		return value
	if isinstance(value, tuple):
		return list(value)
	if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
		return list(value)
	return [value]


def _candidate_list(candidates):
	return ", ".join(
		f"{item.service_id}@{item.directory_identity}"
		for item in candidates
	)


def _call(obj, method_name, default=None):
	if not hasattr(obj, method_name):
		return default
	try:
		return getattr(obj, method_name)()
	except TypeError:
		return default


def _selector_from_properties(properties):
	selector = properties.get("selector")
	if isinstance(selector, dict):
		return dict(selector)
	result = {}
	if "selector_name" in properties:
		result["name"] = properties["selector_name"]
	for source, target in (
		("selector_aliases", "aliases"),
		("selector_resources", "resources"),
	):
		if source in properties:
			result[target] = _as_list(properties[source])
	for source in ("resource", "device_id"):
		if source in properties and "resources" not in result:
			result["resources"] = [properties[source]]
	return result


def _service_properties(service_info):
	properties = _call(service_info, "get_properties", {})
	if properties is None:
		return {}
	return dict(properties)
