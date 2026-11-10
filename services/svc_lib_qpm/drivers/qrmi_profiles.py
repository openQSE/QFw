# What differs per provider in QrmiDriver. The driver owns the QRMI resource
# and its job lifecycle; a profile owns what the provider's device reports and
# how a circuit reaches it. Imports stay lazy so services can start without
# the provider's libraries.

from .base_driver import pasqal_backend_info
from defw_exception import DEFwExecutionError
import json


class QrmiProfile:
	provider = None
	capabilities = frozenset()

	def __init__(self, descriptor=None):
		self._descriptor = dict(descriptor or {})

	def serves(self, call, driver_capabilities):
		return call in driver_capabilities

	def is_complete_target(self, target_data):
		# Whether a target() payload is whole enough to cache.
		return True

	def device_info(self, driver):
		raise NotImplementedError

	def coupling_graph(self, driver):
		raise NotImplementedError

	def calibration_snapshot(self, driver):
		raise NotImplementedError

	def dynamic_backend_info(self, driver):
		raise NotImplementedError

	def backend_info(self, driver, credential=None):
		raise NotImplementedError

	def run_circuit(self, driver, source):
		raise NotImplementedError

	def vendor_timing(self, driver, job_id, credential=None):
		# The provider's own account of the job, or {} when it has none.
		return {}


class IqmQrmiProfile(QrmiProfile):
	provider = "iqm"

	def device_info(self, driver):
		from qhw_iqm import normalize_device
		return normalize_device(
			driver._arch_raw(), device_id=driver._device_id())

	def coupling_graph(self, driver):
		from qhw_iqm import normalize_coupling
		return normalize_coupling(
			driver._arch_raw(), device_id=driver._device_id())

	def calibration_snapshot(self, driver):
		# target() carries the IQM calibration_set + quality_metrics; feed them
		# (with the dynamic architecture) to qhw-iqm — the same normalizer the
		# native svc_iqm_qpm path uses — to build a qhw-calibration-v1 record.
		# Selecting a specific calibration_set_id is a follow-up; this returns
		# the resource's current/default calibration.
		from qhw_iqm import normalize_calibration
		target = driver._target()
		raw = {
			"dynamic_architecture":
				target.get("dynamic_quantum_architecture") or {},
			"calibration_set": target.get("calibration_set") or {},
			"quality_metric_set": target.get("quality_metrics") or {},
		}
		return normalize_calibration(raw, device_id=driver._device_id())

	def dynamic_backend_info(self, driver):
		# The dynamic architecture as-is, matching the native svc_iqm_qpm shape
		# (a provider dict, not a qhw record).
		return {
			"backend": self._descriptor.get("provider", "iqm"),
			"metadata_supported": True,
			"dynamic_architecture":
				driver._target().get("dynamic_quantum_architecture") or {},
		}

	def backend_info(self, driver, credential=None):
		# Native composite shape (mirrors svc_iqm_qpm.get_backend_info): the
		# provider fields plus an embedded qhw device record. The static
		# architecture is empty against QRMI older than 0.22.0, which did not
		# report it at all.
		from qhw_iqm import normalize_device
		target = driver._target(credential=credential)
		dynamic = target.get("dynamic_quantum_architecture") or {}
		return {
			"backend": self._descriptor.get("provider", "iqm"),
			"metadata_supported": True,
			"static_architecture": driver._static_arch(target),
			"active_qubits": dynamic.get("qubits") or [],
			"calibration_set_id": dynamic.get("calibration_set_id"),
			"qhw_device": normalize_device(
				driver._arch_raw(credential), device_id=driver._device_id()),
		}

	def run_circuit(self, driver, source):
		return driver._run_iqm_circuit(source)

	def vendor_timing(self, driver, job_id, credential=None):
		# QRMI returns the measurements alone from task_result, and the job's
		# timeline, the server's timestamp per phase, only as the text of
		# task_logs, which util.iqm_timeline reads.
		from util.iqm_timeline import durations_from_task_logs
		return durations_from_task_logs(
			driver._task_logs(job_id, credential=credential))


class IbmQrmiProfile(QrmiProfile):
	provider = "ibm"

	def is_complete_target(self, target_data):
		# IBM target() returns null for configuration or properties on
		# failure, and a payload cached then would stick after IBM recovers.
		return (
			isinstance(target_data, dict)
			and target_data.get("configuration") is not None
			and target_data.get("properties") is not None)

	@staticmethod
	def _configuration(driver, credential=None):
		configuration = driver._target(
			credential=credential).get("configuration") or {}
		if not configuration:
			raise DEFwExecutionError("QRMI failed to retrieve the backend data")
		return configuration

	def device_info(self, driver):
		from qhw_ibm import normalize_device
		return normalize_device(
			self._configuration(driver), device_id=driver._device_id())

	def coupling_graph(self, driver):
		from qhw_ibm import normalize_coupling
		return normalize_coupling(
			self._configuration(driver), device_id=driver._device_id())

	def calibration_snapshot(self, driver):
		# The calibration normalizer reads the properties sub-dict
		# (qubits, gates, last_update_date).
		from qhw_ibm import normalize_calibration
		properties = driver._target().get("properties") or {}
		if not properties:
			raise DEFwExecutionError("QRMI failed to retrieve the backend data")
		return normalize_calibration(properties, device_id=driver._device_id())

	def dynamic_backend_info(self, driver):
		# IBM target() has no live dynamic state; configuration and
		# properties are static snapshots with no calibration_set_id or
		# active qubit list equivalent.
		return {
			"backend": "ibm",
			"metadata_supported": False,
			"dynamic_architecture": {},
		}

	def backend_info(self, driver, credential=None):
		from qhw_ibm import normalize_device
		target = driver._target(credential=credential)
		configuration = target.get("configuration") or {}
		if not configuration:
			raise DEFwExecutionError("QRMI failed to retrieve the backend data")
		qhw_device = normalize_device(
			configuration, device_id=driver._device_id())
		qubits_len = len(qhw_device.get("qubits", []))
		n_qubits = configuration.get("n_qubits", qubits_len)
		# The raw configuration and properties together are what
		# _build_backend_target converts to the ISA target, so a client
		# that builds its own target gets both here.
		return {
			"backend": "ibm",
			"metadata_supported": True,
			"static_architecture": configuration,
			"backend_properties": target.get("properties") or None,
			"active_qubits": list(range(n_qubits)),
			"calibration_set_id": None,
			"qhw_device": qhw_device,
		}

	def run_circuit(self, driver, source):
		return driver._run_ibm_circuit(source)


class PasqalQrmiProfile(QrmiProfile):
	provider = "pasqal"
	# No qhw normalizer reads a Pulser device, so the qhw introspection calls
	# are a gap for it rather than an IQM parse of the wrong payload.
	capabilities = frozenset({
		"get_backend_info",
		"run_circuit",
		"get_task_timing",
		"get_task_metadata",
	})

	def serves(self, call, driver_capabilities):
		return call in self.capabilities

	def backend_info(self, driver, credential=None):
		# target() is Pasqal Cloud's device-spec entries, read for each call
		# rather than through the target cache, so a client builds against the
		# current calibration. An emulator's are public; a QPU's need a login,
		# the reservation's credential when the client names its reservation
		# and the service's otherwise (_pasqal_cloud_settings).
		try:
			entries = json.loads(
				driver._qpu(credential=credential).target().value)
		except Exception as exc:
			raise driver._qrmi_error(
				exc, "failed to read QRMI target()") from exc
		return pasqal_backend_info(
			str(driver._qc_alias(credential=credential) or "").split(",")[0],
			entries)

	def run_circuit(self, driver, source):
		return driver._run_pasqal_sequence(source)


PROFILES = {
	profile.provider: profile
	for profile in (IqmQrmiProfile, IbmQrmiProfile, PasqalQrmiProfile)}


def profile_for(descriptor):
	# A provider QRMI has no profile for takes the IQM one, as the driver
	# always has.
	provider = str((descriptor or {}).get("provider") or "iqm").lower()
	return PROFILES.get(provider, IqmQrmiProfile)(descriptor)
