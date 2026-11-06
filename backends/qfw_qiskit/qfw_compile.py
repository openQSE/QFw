# Compiling ahead of time for one device, on the caller's own CPUs. Imports no
# DEFw, so a compile job on a CPU node can use it with a device snapshot.

import logging
import re
from importlib.metadata import PackageNotFoundError, version

from qiskit import QuantumCircuit

from util.compile_profiles import DeviceNotDescribed, compile_profile
from util.execution_error import DEFwExecutionError
from util.isa import check_calibration, check_device, make_marker, read_marker

from .qfw_metadata import (
	get_isa_marker, get_qubit_mapping, set_isa_marker, set_qubit_mapping)
from .qfw_target import TARGET_FALLBACK


def _mqt_core_version():
	try:
		raw = version('mqt-core')
	except PackageNotFoundError:
		return None, None
	match = re.match(r'(\d+(?:\.\d+)*)', raw)
	if match is None:
		return raw, None
	return raw, tuple(int(p) for p in match.group(1).split('.'))


def _compiler_name(compiler):
	package = {'qiskit': 'qiskit', 'mqt': 'mqt-core'}[compiler]
	try:
		return f"{compiler} {version(package)}"
	except PackageNotFoundError:
		return compiler


class DeviceCompiler:
	"""Compile, check and mark circuits for the device a host describes. The
	host supplies published(name), target, target_kind, target_source,
	telemetry, backend_info and _refresh_target()."""

	def isa_device(self):
		"""The (provider, device_id) a circuit is marked with for this QPM."""
		provider = self.published("isa_provider")
		device_id = self.published("isa_device_id")
		if not provider or not device_id:
			raise DEFwExecutionError(
				"this QPM does not take a circuit compiled ahead of time")
		return provider, device_id

	def _device_provider(self):
		return (self.published("isa_provider") or
			self.published("device_provider") or
			self.published("provider"))

	def _require_a_device_target(self):
		self.target
		if self.target_kind == TARGET_FALLBACK:
			raise DEFwExecutionError(
				"compile needs a device's target with its connectivity, "
				f"and this backend holds {self.target_source}")

	def _read_compile_device(self, profile):
		if profile.compiles_against_target:
			self._require_a_device_target()
		return profile.read_device(self)

	def compile(self, circuits, compiler='qiskit', *, refresh_target=False,
		    **kwargs):
		"""Compile for this backend's device, on this process's CPUs. The
		result is checked against the device and marked for it, so a QPM
		runs it as it stands or refuses it. A simulator's result is not
		marked. `refresh_target` reads the device again first."""
		if compiler not in self.COMPILERS:
			raise ValueError(
				f"unknown compiler '{compiler}'; expected 'qiskit' or 'mqt'")
		if refresh_target:
			self._refresh_target()
		profile = compile_profile(self._device_provider())
		if profile is None:
			return self._compile_to_target(compiler, circuits, kwargs)
		try:
			device = self._read_compile_device(profile)
		except DeviceNotDescribed as exc:
			logging.warning(
				f"QFw backend: {exc}; compiling to backend.target unmarked, "
				"so the QPM may compile it again")
			return self._compile_to_target(compiler, circuits, kwargs)
		single = isinstance(circuits, QuantumCircuit)
		batch = [circuits] if single else list(circuits)
		checked = [
			self._check_and_mark(compiled, labels, compiler, profile, device)
			for compiled, labels in self._compile_batch(
				batch, compiler, profile, device, kwargs)]
		return checked[0] if single else checked

	def _compile_to_target(self, compiler, circuits, options):
		self._require_a_device_target()
		return self.COMPILERS[compiler](self, circuits, **options)

	def _compile_batch(self, circuits, compiler, profile, device, options):
		if compiler == 'qiskit':
			return profile.compile_all(
				circuits, device,
				[get_qubit_mapping(circuit) for circuit in circuits], **options)
		labels = profile.labels_for_target(device)
		return [
			(self._compile_with_mqt(circuit, **options), labels)
			for circuit in circuits]

	def _check_and_mark(self, compiled, labels, compiler, profile, device):
		if not isinstance(compiled, QuantumCircuit):
			raise DEFwExecutionError(
				f"the {compiler} compiler returned {type(compiled).__name__}, "
				"which QFw cannot check against the device")
		compiled = compiled.copy()
		set_qubit_mapping(compiled, labels)
		device_id = self.published("isa_device_id")
		marker = make_marker(
			self._device_provider(), device_id or self._device_provider(),
			profile.calibration(device), labels, _compiler_name(compiler))
		profile.validate(compiled, device, marker)
		if device_id is None:
			logging.warning(
				"QFw backend: this QPM does not take a circuit compiled ahead "
				"of time, so it may compile this one again")
			return compiled
		return set_isa_marker(compiled, marker)

	def check_isa(self, circuits):
		"""Refuse now what this QPM would refuse after a queue wait: a
		circuit marked for another device, for a calibration the device's
		policy will not run, or not native to the device as it stands."""
		provider, device_id = self.isa_device()
		profile = compile_profile(provider)
		if profile.compiles_against_target:
			self._refresh_target()
		device = self._read_compile_device(profile)
		current = (profile.calibration(device) or {}).get("value")
		if isinstance(circuits, QuantumCircuit):
			circuits = [circuits]
		for circuit in circuits:
			marker = read_marker({"isa": get_isa_marker(circuit)})
			if marker is None:
				raise DEFwExecutionError(
					f"circuit {circuit.name} is not marked as compiled for "
					"a device")
			check_device(
				marker, self.published("circuit_stages"), device_id, provider)
			check_calibration(
				marker, current, self.published("calibration_policy"))
			profile.validate(circuit, device, marker)

	def _compile_with_qiskit(self, circuits, **kwargs):
		from qiskit import transpile
		return transpile(circuits, target=self.target, **kwargs)

	def _compile_with_mqt(self, circuits, **kwargs):
		from mqt.core import mlir

		# Version check the installed MQTC.
		# TODO: Once setup/qdmi-requirements.txt pins a newer MQT
		# Core, remove this check and the NOTE in examples/README.md.
		require_mqtc_above = (4, 1, 1)
		mqtc_ver_str, mqtc_ver = _mqt_core_version()
		if mqtc_ver is not None and mqtc_ver <= require_mqtc_above:
			require_mqtc_str = '.'.join(str(p) for p in require_mqtc_above)
			raise RuntimeError(
				f"The 'mqt' compiler requires an MQT Core newer than "
				f"{require_mqtc_str}, but {mqtc_ver_str} is installed. "
				"See notes for qfw_qiskit_simple_w_mqt.sh in "
				"examples/README.md for more information.")
		return mlir.compile_program(circuits, target=self.target, **kwargs)

	COMPILERS = {
		'qiskit': _compile_with_qiskit,
		'mqt': _compile_with_mqt,
	}
