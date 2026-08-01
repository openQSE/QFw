import logging
import os
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
LOCAL_DIRSVC_ENDPOINT_ENV = "QFW_LOCAL_DIRSVC_ENDPOINT"
SITE_DIRSVC_ENDPOINTS_ENV = "QFW_SITE_DIRSVC_ENDPOINTS"
RESOLVER_SCOPE_ORDER_ENV = "QFW_QPM_RESOLVER_SCOPE_ORDER"
DIRECT_ENDPOINT_FALLBACK_ENV = "QFW_QPM_DIRECT_ENDPOINT_FALLBACK"
DIRECT_QPM_ENDPOINT_ENV = "QFW_DIRECT_QPM_ENDPOINT"
DEFAULT_SCOPE_ORDER = ("site", "allocation-local", "direct")

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


def binding_name_for_category(api_category, binding_name=None):
	if binding_name:
		return binding_name
	return API_CATEGORY_BINDINGS.get(api_category, api_category)


class QPMResolverError(DEFwReserveError):
	pass


class QPMAmbiguousResolutionError(QPMResolverError):
	pass


class QPMStaleGenerationError(QPMResolverError):
	pass


class QPMUnsupportedConfigurationError(QPMResolverError):
	pass


class QPMProviderPolicyError(QPMResolverError):
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
	latest_generation: Optional[int] = None
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
		return binding_name_for_category(self.api_category, self.binding_name)


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
		if hasattr(self._defw, "connect_to_endpoint"):
			return self._defw.connect_to_endpoint(
				resolved.endpoint,
				resolved.api_binding,
			)
		if resolved.directory_scope == "direct":
			raise QPMUnsupportedConfigurationError(
				"direct QPM endpoint resolution requires DEFw "
				"connect_to_endpoint support")
		raise DEFwReserveError(
			f"resolved QPM {resolved.service_id!r} has no DEFw binding")


class DEFwDirectoryClient:
	def __init__(self, endpoint, defw_module=defw):
		self.endpoint = endpoint
		self._defw = defw_module
		self._client = None

	def resolve_service(self, **kwargs):
		client = self._directory_client()
		if hasattr(client, "resolve_service"):
			return client.resolve_service(**kwargs)
		if hasattr(client, "get_services"):
			return client.get_services(
				kwargs.get("service_name", DEFAULT_SERVICE_NAME),
				kwargs.get("qpm_type", -1),
				kwargs.get("qpm_capability", -1),
			)
		raise QPMUnsupportedConfigurationError(
			f"site directory endpoint {self.endpoint!r} does not expose "
			"resolve_service() or get_services()")

	def _directory_client(self):
		if self._client is not None:
			return self._client
		if hasattr(self._defw, "connect_to_directory"):
			self._client = self._defw.connect_to_directory(self.endpoint)
			return self._client
		if hasattr(self._defw, "connect_to_endpoint"):
			self._client = self._defw.connect_to_endpoint(
				self.endpoint,
				QPMApiBinding(
					binding_name="directory",
					client_class="DEFwResMgr",
					service_module="svc_resmgr",
					service_class="DEFwResMgr",
				),
			)
			return self._client
		raise QPMUnsupportedConfigurationError(
			"site-scoped QPM resolution requires a DEFw directory "
			"client factory or endpoint binding support")


class DirectEndpointDirectory:
	def __init__(self, endpoint):
		self.endpoint = endpoint

	def resolve_service(self, **kwargs):
		return {
			"directory_scope": "direct",
			"directory_identity": "direct-endpoint",
			"service_record": {
				"service_id": str(self.endpoint),
				"service_name": kwargs.get("service_name", DEFAULT_SERVICE_NAME),
				"service_type": kwargs.get("service_type", DEFAULT_SERVICE_TYPE),
				"endpoint": self.endpoint,
				"selector": {},
			},
			"selected_api_binding": {
				"binding_name": kwargs.get("binding_name", "execution"),
				"client_module": "api_qpm",
				"client_class": "QPM",
				"service_class": "QPM",
				"version": 1,
			},
		}


class QPMResolver:
	def __init__(self, directories, connector=None, sleeper=sleep,
				 selection_order=None, allow_ambiguous=False):
		self._directories = list(directories)
		self._connector = connector or DEFwQPMConnector()
		self._sleep = sleeper
		self._selection_order = list(selection_order or [])
		self._allow_ambiguous = allow_ambiguous

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

	@classmethod
	def from_environment(cls, rmgr=None, defw_module=defw, sleeper=sleep,
						 directory_client_factory=None):
		directories = []
		local_endpoint = os.environ.get(LOCAL_DIRSVC_ENDPOINT_ENV)
		if rmgr is not None:
			directories.append(DirectoryScope(
				name="allocation-local",
				scope="allocation-local",
				client=rmgr,
				endpoint=local_endpoint,
				identity=local_endpoint or "allocation-local",
				priority=100,
			))
		for index, endpoint in enumerate(
			_split_env_list(os.environ.get(SITE_DIRSVC_ENDPOINTS_ENV))):
			client = (
				directory_client_factory(endpoint)
				if directory_client_factory is not None else
				DEFwDirectoryClient(endpoint, defw_module)
			)
			directories.append(DirectoryScope(
				name=f"site-{index}",
				scope="site",
				client=client,
				endpoint=endpoint,
				identity=endpoint,
				priority=50,
			))
		if _env_enabled(DIRECT_ENDPOINT_FALLBACK_ENV):
			endpoint = os.environ.get(DIRECT_QPM_ENDPOINT_ENV)
			if endpoint:
				directories.append(DirectoryScope(
					name="direct",
					scope="direct",
					client=DirectEndpointDirectory(endpoint),
					endpoint=endpoint,
					identity="direct-endpoint",
					priority=-100,
				))
		order = _split_env_list(os.environ.get(RESOLVER_SCOPE_ORDER_ENV))
		if not order:
			order = list(DEFAULT_SCOPE_ORDER)
		return cls(
			directories,
			DEFwQPMConnector(defw_module),
			sleeper,
			selection_order=order,
		)

	def connect(self, timeout=10, require_current_generation=True, **kwargs):
		resolved = self.resolve(timeout=timeout, **kwargs)
		if require_current_generation:
			self._reject_stale_generation(resolved)
		return self._connector.connect(resolved)

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
				qpm_type=request.qpm_type,
				qpm_capability=request.qpm_capability,
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
			latest_generation=(
				record.get("latest_generation") or
				service.get("latest_generation") or
				properties.get("latest_generation")
			),
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
			latest_generation=properties.get("latest_generation"),
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
			key=self._selection_sort_key,
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
			self._reject_ambiguous_resolution(
				matching_provider, request, provider=request.provider)
			logging.debug(
				f"selected QPM impl '{request.provider}' "
				f"({len(matching_provider)} of "
				f"{len(ordered)} match(es))")
			return matching_provider[0]
		self._reject_ambiguous_resolution(ordered, request)
		return ordered[0]

	def _reject_ambiguous_resolution(self, ordered, request, provider=None):
		if self._allow_ambiguous or not ordered:
			return
		tied = [
			item for item in ordered
			if self._selection_rank(item) == self._selection_rank(ordered[0])
		]
		if len(tied) <= 1:
			return
		provider_text = (
			f" provider {provider!r}"
			if provider is not None else "")
		raise QPMAmbiguousResolutionError(
			f"ambiguous QPM{provider_text} resolution for "
			f"{request.service_type}/{request.binding_filter()}: "
			f"{_candidate_list(tied)}")

	def _selection_sort_key(self, candidate):
		return (
			self._scope_order(candidate),
			-candidate.directory_priority,
			candidate.discovery_index,
		)

	def _selection_rank(self, candidate):
		return (
			self._scope_order(candidate),
			candidate.directory_priority,
		)

	def _scope_order(self, candidate):
		if not self._selection_order:
			return 0
		names = (
			candidate.directory_scope,
			candidate.directory_identity,
		)
		for name in names:
			if name in self._selection_order:
				return self._selection_order.index(name)
		return len(self._selection_order)

	def _reject_stale_generation(self, resolved):
		if resolved.generation is None:
			return
		latest = self._latest_generation(resolved)
		if latest is None:
			return
		try:
			current = int(resolved.generation)
			latest = int(latest)
		except (TypeError, ValueError):
			return
		if latest > current:
			raise QPMStaleGenerationError(
				f"stale QPM binding for {resolved.service_id}: "
				f"generation {current} is older than {latest}")

	def _latest_generation(self, resolved):
		if resolved.latest_generation is not None:
			return resolved.latest_generation
		for directory in self._directories:
			if not self._directory_matches_resolved(directory, resolved):
				continue
			client = directory.client
			for method_name in ("get_service_generation", "get_generation"):
				if not hasattr(client, method_name):
					continue
				try:
					return getattr(client, method_name)(resolved.service_id)
				except TypeError:
					continue
		return None

	def _directory_matches_resolved(self, directory, resolved):
		identity = directory.identity or directory.name
		return (
			directory.scope == resolved.directory_scope and
			identity == resolved.directory_identity
		)


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


def _split_env_list(value):
	if not value:
		return []
	return [item.strip() for item in value.replace(";", ",").split(",")
		if item.strip()]


def _env_enabled(name):
	value = os.environ.get(name, "")
	return value.strip().lower() in {"1", "true", "yes", "on", "y"}


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
