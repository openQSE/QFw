import logging
import os
import uuid
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
DIRECT_QPM_SERVICE_MODULE_ENV = "QFW_DIRECT_QPM_SERVICE_MODULE"
DIRECT_QPM_SERVICE_CLASS_ENV = "QFW_DIRECT_QPM_SERVICE_CLASS"
SIMULATOR_FALLBACK_ENV = "QFW_QPM_ALLOW_SIMULATOR_FALLBACK"
DEFAULT_SCOPE_ORDER = ("site", "allocation-local", "direct")
SCOPE_ALIASES = {
	"local": "allocation-local",
	"allocation-local": "allocation-local",
	"site": "site",
	"direct": "direct",
}
QPM_TYPE_HARDWARE = 1 << 0
QPM_TYPE_SIMULATOR = 1 << 1
SIMULATOR_PROVIDERS = {"simulator", "nwqsim", "tnqvm", "qb"}
PROVIDER_SERVICE_MODULES = {
	"iqm": "svc_iqm_qpm.svc_qpm",
	"fake-iqm": "svc_fake_iqm_qpm.svc_qpm",
	"shim": "svc_lib_qpm.svc_qpm",
	"nwqsim": "svc_nwqsim_qpm.svc_qpm",
	"tnqvm": "svc_tnqvm_qpm.svc_qpm",
	"qb": "svc_qb_qpm.svc_qpm",
}
ZERO_UUID = str(uuid.UUID(int=0))

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


class QPMInvalidDirectoryRecordError(QPMResolverError):
	pass


class QPMSimulatorFallbackPolicyError(QPMResolverError):
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
	qpm_capabilities: Any = None
	provider: Optional[str] = None
	allow_simulator_fallback: bool = False

	def __post_init__(self):
		if self.qpm_capabilities is None:
			object.__setattr__(
				self, "qpm_capabilities", self.qpm_capability)
		elif self.qpm_capability in (-1, None):
			object.__setattr__(
				self, "qpm_capability", self.qpm_capabilities)

	def binding_filter(self):
		return binding_name_for_category(self.api_category, self.binding_name)


class DEFwQPMConnector:
	def __init__(self, defw_module=defw):
		self._defw = defw_module

	def connect(self, resolved):
		if (hasattr(self._defw, "connect_to_binding") and
				_can_use_binding_connector(resolved)):
			return self._defw.connect_to_binding(
				_defw_binding_record(resolved))
		if resolved.directory_scope == "direct":
			if hasattr(self._defw, "connect_to_endpoint"):
				return self._defw.connect_to_endpoint(
					resolved.endpoint,
					resolved.api_binding,
				)
			raise QPMUnsupportedConfigurationError(
				"direct QPM endpoint resolution requires DEFw "
				"connect_to_binding or connect_to_endpoint support")
		if hasattr(self._defw, "connect_to_endpoint"):
			return self._defw.connect_to_endpoint(
				resolved.endpoint,
				resolved.api_binding,
			)
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
		if hasattr(client, "resolve_services"):
			return client.resolve_services(**kwargs)
		raise QPMUnsupportedConfigurationError(
			f"site directory endpoint {self.endpoint!r} does not expose "
			"resolve_service() or resolve_services()")

	def _directory_client(self):
		if self._client is not None:
			return self._client
		if hasattr(self._defw, "connect_to_directory"):
			self._client = self._defw.connect_to_directory(self.endpoint)
			return self._client
		if hasattr(self._defw, "connect_to_binding"):
			self._client = self._defw.connect_to_binding(
				_defw_directory_binding_record(self.endpoint))
			return self._client
		if hasattr(self._defw, "connect_to_endpoint"):
			self._client = self._defw.connect_to_endpoint(
				self.endpoint,
				_directory_api_binding(),
			)
			return self._client
		raise QPMUnsupportedConfigurationError(
			"site-scoped QPM resolution requires a DEFw directory "
			"client factory or binding support")


class DirectEndpointDirectory:
	def __init__(self, endpoint, provider=None, service_module=None,
		     service_class="QPM"):
		self.endpoint = endpoint
		self.provider = provider
		self.service_module = service_module
		self.service_class = service_class

	def resolve_service(self, **kwargs):
		endpoint = _endpoint_record_from_value(
			self.endpoint,
			default_name="direct-qpm",
		)
		if endpoint is None:
			raise QPMUnsupportedConfigurationError(
				f"direct QPM endpoint {self.endpoint!r} must include "
				"a listen port")
		provider = kwargs.get("provider") or self.provider
		service_module = (
			self.service_module or
			_provider_service_module(provider)
		)
		properties = {}
		if provider:
			properties["provider"] = provider
		if kwargs.get("qpm_type") not in (-1, None):
			properties["qpm_type"] = kwargs.get("qpm_type")
		qpm_capabilities = kwargs.get(
			"qpm_capabilities", kwargs.get("qpm_capability"))
		if qpm_capabilities not in (-1, None):
			properties["qpm_capabilities"] = qpm_capabilities
		return {
			"directory_scope": "direct",
			"directory_identity": "direct-endpoint",
			"service_record": {
				"service_id": str(self.endpoint),
				"service_name": kwargs.get("service_name", DEFAULT_SERVICE_NAME),
				"service_type": kwargs.get("service_type", DEFAULT_SERVICE_TYPE),
				"runtime_id": endpoint["runtime_id"],
				"endpoint": self.endpoint,
				"selector": {},
				"properties": properties,
				"qpm_type": properties.get("qpm_type", -1),
				"qpm_capabilities": properties.get(
					"qpm_capabilities", -1),
			},
			"selected_api_binding": {
				"binding_name": kwargs.get("binding_name", "execution"),
				"client_module": "api_qpm",
				"client_class": "QPM",
				"service_module": service_module,
				"service_class": self.service_class,
				"version": 1,
			},
		}


class QPMResolver:
	def __init__(self, directories, connector=None, sleeper=sleep,
				 selection_order=None, allow_ambiguous=False):
		self._directories = list(directories)
		self._connector = connector or DEFwQPMConnector()
		self._sleep = sleeper
		self._selection_order = _normalize_scope_order(
			selection_order or [])
		self._allow_ambiguous = allow_ambiguous

	@classmethod
	def from_directory_service(cls, dirsvc, defw_module=defw, sleeper=sleep):
		directory = DirectoryScope(
			name="allocation-local",
			scope="allocation-local",
			client=dirsvc,
			identity="allocation-local",
			priority=100,
		)
		return cls([directory], DEFwQPMConnector(defw_module), sleeper)

	@classmethod
	def from_environment(cls, dirsvc=None, defw_module=defw, sleeper=sleep,
						 directory_client_factory=None):
		directories = []
		order = _split_env_list(os.environ.get(RESOLVER_SCOPE_ORDER_ENV))
		if not order:
			order = list(DEFAULT_SCOPE_ORDER)
		order = _normalize_scope_order(order)
		local_endpoint = os.environ.get(LOCAL_DIRSVC_ENDPOINT_ENV)
		site_endpoints = _split_env_list(os.environ.get(
			SITE_DIRSVC_ENDPOINTS_ENV))
		bound_site_endpoint = None
		bound_dirsvc_is_local = bool(local_endpoint) or not site_endpoints
		if dirsvc is not None and not local_endpoint and site_endpoints:
			bound_site_endpoint = site_endpoints[0]
		if dirsvc is not None and bound_dirsvc_is_local and _names_allowed(
				("allocation-local", local_endpoint), order):
			directories.append(DirectoryScope(
				name="allocation-local",
				scope="allocation-local",
				client=dirsvc,
				endpoint=local_endpoint,
				identity=local_endpoint or "allocation-local",
				priority=100,
			))
		for index, endpoint in enumerate(site_endpoints):
			name = f"site-{index}"
			if not _names_allowed(("site", endpoint, name), order):
				continue
			if dirsvc is not None and endpoint == bound_site_endpoint:
				client = dirsvc
			else:
				client = (
					directory_client_factory(endpoint)
					if directory_client_factory is not None else
					DEFwDirectoryClient(endpoint, defw_module)
				)
			directories.append(DirectoryScope(
				name=name,
				scope="site",
				client=client,
				endpoint=endpoint,
				identity=endpoint,
				priority=50,
			))
		if (_env_enabled(DIRECT_ENDPOINT_FALLBACK_ENV) and
				_names_allowed(("direct", "direct-endpoint"), order)):
			endpoint = os.environ.get(DIRECT_QPM_ENDPOINT_ENV)
			if endpoint:
				provider = os.environ.get(
					QPM_IMPL_ENV, DEFAULT_QPM_IMPL).strip()
				directories.append(DirectoryScope(
					name="direct",
					scope="direct",
					client=DirectEndpointDirectory(
						endpoint,
						provider=provider,
						service_module=os.environ.get(
							DIRECT_QPM_SERVICE_MODULE_ENV,
							_provider_service_module(provider)),
						service_class=os.environ.get(
							DIRECT_QPM_SERVICE_CLASS_ENV,
							"QPM"),
					),
					endpoint=endpoint,
					identity="direct-endpoint",
					priority=-100,
				))
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
		if ("allow_simulator_fallback" not in kwargs and
				_env_enabled(SIMULATOR_FALLBACK_ENV)):
			kwargs["allow_simulator_fallback"] = True
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
			if (not directory.enabled or
					not self._directory_allowed(directory)):
				continue
			records = self._query_directory(directory, request)
			for record in records:
				candidate = self._normalize_record(
					directory,
					record,
					request,
					len(candidates),
				)
				if (self._candidate_allowed(candidate) and
						self._matches_request(candidate, request)):
					candidates.append(candidate)
		return candidates

	def _query_directory(self, directory, request):
		client = directory.client
		if client is None:
			return []
		filters = self._query_filters(request)
		if hasattr(client, "resolve_service"):
			return _as_list(client.resolve_service(**filters))
		if hasattr(client, "resolve_services"):
			return _as_list(client.resolve_services(**filters))
		return []

	def _query_filters(self, request):
		return {
			"service_name": request.service_name,
			"service_type": request.service_type,
			"binding_name": request.binding_filter(),
			"selector_resource": request.selector_resource,
			"selector_alias": request.selector_alias,
			"api_category": request.api_category,
			"qpm_type": request.qpm_type,
			"qpm_capability": request.qpm_capability,
			"qpm_capabilities": request.qpm_capabilities,
			"provider": request.provider,
		}

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
		raise QPMInvalidDirectoryRecordError(
			"QPM directory resolution requires binding-aware directory "
			"records; legacy DEFwServiceInfo entries are not supported")

	def _normalize_directory_record(self, directory, record, request,
					discovery_index):
		service = record.get("service_record", record)
		binding = (
			record.get("selected_api_binding") or
			record.get("selected_binding") or
			record.get("api_binding")
		)
		_validate_directory_record(service, binding, record)
		api_binding = _api_binding_from_mapping(binding, request)
		properties = dict(service.get("properties") or {})
		for key in (
				"qpm_type", "qpm_capabilities", "qpm_capability",
				"capability"):
			if key in service and key not in properties:
				properties[key] = service[key]
		if "qpm_capabilities" not in properties and \
				"qpm_capability" in properties:
			properties["qpm_capabilities"] = properties["qpm_capability"]
		selector = dict(service.get("selector") or {})
		endpoint = service.get("endpoint") or record.get("endpoint")
		service_id = service.get("service_id") or properties.get("service_id")
		return QPMResolvedBinding(
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

	def _matches_request(self, candidate, request):
		if request.service_type and candidate.service_type != request.service_type:
			return False
		if not _candidate_bits_match(
				candidate, ("qpm_type",), request.qpm_type):
			return False
		if not _candidate_bits_match(
				candidate,
				("qpm_capabilities", "qpm_capability"),
				request.qpm_capabilities):
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
		candidates = self._apply_simulator_fallback_policy(
			candidates, request)
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

	def _apply_simulator_fallback_policy(self, candidates, request):
		if _simulator_fallback_allowed(request):
			return candidates
		if not _request_requires_hardware(request):
			return candidates
		simulator_candidates = [
			candidate for candidate in candidates
			if _candidate_is_simulator(candidate)
		]
		if not simulator_candidates:
			return candidates
		non_simulator_candidates = [
			candidate for candidate in candidates
			if not _candidate_is_simulator(candidate)
		]
		if non_simulator_candidates:
			return non_simulator_candidates
		raise QPMSimulatorFallbackPolicyError(
			"explicit simulator fallback policy required for hardware "
			f"QPM request: {_candidate_list(simulator_candidates)}")

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
			_normalize_scope_name(candidate.directory_scope),
			_normalize_scope_name(candidate.directory_identity),
		)
		for name in names:
			if name in self._selection_order:
				return self._selection_order.index(name)
		return len(self._selection_order)

	def _directory_allowed(self, directory):
		if not self._selection_order:
			return True
		return _names_allowed((
			_normalize_scope_name(directory.scope),
			_normalize_scope_name(directory.identity),
			_normalize_scope_name(directory.name),
		), self._selection_order)

	def _candidate_allowed(self, candidate):
		if not self._selection_order:
			return True
		return _names_allowed((
			_normalize_scope_name(candidate.directory_scope),
			_normalize_scope_name(candidate.directory_identity),
		), self._selection_order)

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
		for directory in self._directories:
			if not self._directory_matches_resolved(directory, resolved):
				continue
			client = directory.client
			for method_name in ("get_service_generation", "get_generation"):
				if not hasattr(client, method_name):
					continue
				try:
					latest = getattr(client, method_name)(resolved.service_id)
				except TypeError:
					continue
				if latest is not None:
					return latest
		if resolved.latest_generation is not None:
			return resolved.latest_generation
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


def _validate_directory_record(service, binding, record):
	if not isinstance(service, dict):
		raise QPMInvalidDirectoryRecordError(
			"directory service record must be a mapping")
	properties = service.get("properties") or {}
	service_id = service.get("service_id") or properties.get("service_id")
	if not service_id:
		raise QPMInvalidDirectoryRecordError(
			"directory service record is missing service_id")
	endpoint = service.get("endpoint") or record.get("endpoint")
	if not endpoint:
		raise QPMInvalidDirectoryRecordError(
			f"directory service record {service_id!r} is missing endpoint")
	if binding is None:
		raise QPMInvalidDirectoryRecordError(
			f"directory service record {service_id!r} is missing "
			"selected API binding")
	if not isinstance(binding, dict):
		raise QPMInvalidDirectoryRecordError(
			f"selected API binding for {service_id!r} must be a mapping")
	if not binding.get("binding_name"):
		raise QPMInvalidDirectoryRecordError(
			f"selected API binding for {service_id!r} is missing "
			"binding_name")


def _can_use_binding_connector(resolved):
	return _endpoint_record_from_value(
		resolved.endpoint,
		default_name=resolved.service_name,
		runtime_id=resolved.runtime_id,
	) is not None


def _defw_binding_record(resolved):
	endpoint = _endpoint_record_from_value(
		resolved.endpoint,
		default_name=resolved.service_name,
		runtime_id=resolved.runtime_id,
	)
	if endpoint is None:
		raise QPMUnsupportedConfigurationError(
			f"resolved QPM endpoint for {resolved.service_id!r} must "
			"include address and listen_port")
	return {
		"service_record": {
			"service_id": resolved.service_id,
			"service_name": resolved.service_name,
			"service_type": resolved.service_type,
			"runtime_id": endpoint["runtime_id"],
			"generation": resolved.generation,
			"endpoint": endpoint,
			"selector": dict(resolved.selector_metadata or {}),
			"properties": dict(resolved.properties or {}),
		},
		"selected_binding": _api_binding_to_mapping(resolved.api_binding),
	}


def _defw_directory_binding_record(endpoint):
	endpoint_record = _endpoint_record_from_value(
		endpoint,
		default_name="DEFwDirSvc",
	)
	if endpoint_record is None:
		raise QPMUnsupportedConfigurationError(
			f"site directory endpoint {endpoint!r} must include a "
			"listen port")
	return {
		"service_record": {
			"service_id": f"dirsvc:{endpoint}",
			"service_name": "DEFwDirSvc",
			"service_type": "defw.dirsvc",
			"runtime_id": endpoint_record["runtime_id"],
			"generation": None,
			"endpoint": endpoint_record,
			"selector": {
				"resources": ["DEFwDirSvc"],
				"aliases": ["dirsvc", "directory"],
			},
			"properties": {},
		},
		"selected_binding": _api_binding_to_mapping(
			_directory_api_binding()),
	}


def _directory_api_binding():
	return QPMApiBinding(
		binding_name="directory",
		client_module="api_dirsvc",
		client_class="DEFwDirSvc",
		service_module="svc_dirsvc.svc_dirsvc",
		service_class="DEFwDirSvc",
	)


def _api_binding_to_mapping(api_binding):
	return {
		"binding_name": api_binding.binding_name,
		"client_module": api_binding.client_module,
		"client_class": api_binding.client_class,
		"service_module": api_binding.service_module,
		"service_class": api_binding.service_class,
		"version": api_binding.version,
		"policy_labels": list(api_binding.policy_labels),
	}


def _endpoint_record_from_value(endpoint, default_name=None, runtime_id=None):
	if isinstance(endpoint, dict):
		address = (
			endpoint.get("address") or
			endpoint.get("addr") or
			endpoint.get("host") or
			endpoint.get("hostname")
		)
		listen_port = (
			endpoint.get("listen_port") or
			endpoint.get("listen-port") or
			endpoint.get("port")
		)
		if not address or listen_port is None:
			return None
		try:
			listen_port = int(listen_port)
		except (TypeError, ValueError):
			return None
		name = (
			endpoint.get("node_name") or
			endpoint.get("name") or
			default_name or
			str(address)
		)
		hostname = endpoint.get("hostname") or str(address)
		return {
			"address": str(address),
			"listen_port": listen_port,
			"pid": int(endpoint.get("pid", 0) or 0),
			"node_name": str(name),
			"hostname": str(hostname),
			"runtime_id": (
				runtime_id or
				endpoint.get("runtime_id") or
				endpoint.get("remote_uuid") or
				ZERO_UUID
			),
		}
	if isinstance(endpoint, str):
		parsed = _parse_endpoint_string(endpoint)
		if parsed is None:
			return None
		host, listen_port = parsed
		return {
			"address": host,
			"listen_port": listen_port,
			"pid": 0,
			"node_name": default_name or host,
			"hostname": host,
			"runtime_id": runtime_id or ZERO_UUID,
		}
	return None


def _parse_endpoint_string(endpoint):
	value = endpoint.strip()
	if not value:
		return None
	if "://" in value:
		from urllib.parse import urlparse

		parsed = urlparse(value)
		if not parsed.hostname or parsed.port is None:
			return None
		return parsed.hostname, parsed.port
	if value.count(":") == 1:
		host, port = value.rsplit(":", 1)
		if not host or not port:
			return None
		try:
			return host, int(port)
		except ValueError:
			return None
	return None


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


def _normalize_scope_name(name):
	if name is None:
		return None
	value = str(name).strip()
	return SCOPE_ALIASES.get(value, value)


def _normalize_scope_order(order):
	normalized = []
	for item in order:
		name = _normalize_scope_name(item)
		if name:
			normalized.append(name)
	return normalized


def _names_allowed(names, selection_order):
	for item in names:
		name = _normalize_scope_name(item)
		if name and name in selection_order:
			return True
	return False


def _env_enabled(name):
	value = os.environ.get(name, "")
	return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _request_requires_hardware(request):
	try:
		qpm_type = int(request.qpm_type)
	except (TypeError, ValueError):
		return False
	if qpm_type in (-1, 0):
		return False
	return bool(qpm_type & QPM_TYPE_HARDWARE)


def _simulator_fallback_allowed(request):
	if request.allow_simulator_fallback:
		return True
	if _provider_is_simulator(request.provider):
		return True
	try:
		qpm_type = int(request.qpm_type)
	except (TypeError, ValueError):
		return False
	return bool(qpm_type & QPM_TYPE_SIMULATOR)


def _candidate_bits_match(candidate, property_keys, requested_bits):
	if requested_bits in (-1, None):
		return True
	properties = candidate.properties or {}
	record_bits = None
	for property_key in property_keys:
		record_bits = properties.get(property_key)
		if record_bits not in (-1, None):
			break
	if record_bits in (-1, None):
		return True
	return _bits_match(record_bits, requested_bits)


def _bits_match(record_bits, requested_bits):
	try:
		record_bits = int(record_bits)
		requested_bits = int(requested_bits)
	except (TypeError, ValueError):
		return False
	if requested_bits in (-1, 0):
		return True
	if record_bits in (-1, 0):
		return False
	return (record_bits & requested_bits) == requested_bits


def _candidate_is_simulator(candidate):
	properties = candidate.properties or {}
	for key in ("simulator", "is_simulator"):
		if key in properties:
			return _truthy(properties.get(key))
	if _provider_is_simulator(properties.get("provider")):
		return True
	try:
		qpm_type = int(properties.get("qpm_type", -1))
	except (TypeError, ValueError):
		return False
	return (
		qpm_type not in (-1, 0) and
		bool(qpm_type & QPM_TYPE_SIMULATOR)
	)


def _provider_is_simulator(provider):
	if provider is None:
		return False
	return str(provider).strip().lower() in SIMULATOR_PROVIDERS


def _provider_service_module(provider):
	if provider is None:
		return None
	return PROVIDER_SERVICE_MODULES.get(str(provider).strip().lower())


def _truthy(value):
	if isinstance(value, bool):
		return value
	if value is None:
		return False
	if isinstance(value, (int, float)):
		return value != 0
	return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _candidate_list(candidates):
	return ", ".join(
		f"{item.service_id}@{item.directory_identity}"
		for item in candidates
	)
