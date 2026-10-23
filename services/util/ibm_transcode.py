# Shared IBM Qiskit Sampler input transcode utilities.
#
# Provides building blocks used by qrmi_driver._run_ibm_circuit (and any
# future IBM execution path) to turn an abstract Qiskit circuit into the wire
# format expected by Payload.QiskitPrimitive(input=...).

import json
import logging

from defw_exception import DEFwExecutionError


def _circuit_is_isa(circuit, target):
	# Return True when every (gate, qargs) combination in the circuit is
	# natively supported by the target, meaning the circuit was pre-transpiled
	# by the caller and must not be compiled again.
	#
	# GatesInBasis(target=) checks both gate names and per-qubit-pair support
	# in a single Rust-backed DAG walk.  Qiskit's own preset pass manager uses
	# the same pass to guard the translation sub-stage inside its optimization
	# loop (builtin_plugins.py), but only after layout and routing
	# have already run.  Checking here — before pm.run() — is the only place
	# that prevents the layout stage from overwriting an intentional qubit
	# placement.
	#
	# If the import fails we conservatively return False so compilation still
	# runs rather than silently submitting an untranspiled circuit.
	try:
		from qiskit.transpiler.passes import GatesInBasis
		from qiskit.transpiler import PassManager
	except Exception:
		return False
	pm = PassManager(GatesInBasis(target=target))
	pm.run(circuit)
	return bool(pm.property_set.get("all_gates_in_basis"))


def compile_circuit(circuit, target, compilation_options=None):
	# Transpile an abstract QuantumCircuit to the backend ISA.
	# Uses generate_preset_pass_manager with target forced to the one resolved
	# from the QFw descriptor; backend is cleared so Qiskit doesn't try to
	# re-derive a target from it. compilation_options may supply any other
	# keyword accepted by generate_preset_pass_manager (e.g. optimization_level);
	# a fresh dict is built from it so the caller's object is never mutated.
	#
	# If the circuit is already ISA the layout chosen by the caller is
	# preserved and compilation is skipped entirely.
	if _circuit_is_isa(circuit, target):
		logging.info("Circuit is already ISA for the target; skipping compilation.")
		return circuit

	try:
		from qiskit.transpiler.preset_passmanagers import generate_preset_pass_manager
	except Exception as exc:
		raise DEFwExecutionError(
			f"failed to import qiskit transpiler: {exc}") from exc
	import inspect
	logging.info(f"Compilation options provided to qiskit: {compilation_options}")
	# Build a clean options dict so we never mutate the caller's object.
	# target is always forced to the one resolved from the QFw descriptor;
	# backend is cleared so Qiskit doesn't try to re-derive a target from it.
	options = dict(compilation_options or {})
	options["backend"] = None
	options["target"] = target

	sig = inspect.signature(generate_preset_pass_manager)
	try:
		bound_args = sig.bind_partial(**options)
		pm = generate_preset_pass_manager(**bound_args.arguments)
	except TypeError as exc:
		raise DEFwExecutionError(f"Circuit compilation options invalid: {exc}")
	return pm.run(circuit)


def qiskit_sampler_input_json(isa_circuit, param_values=None, shots=None):
	# Encode an ISA QuantumCircuit into the QiskitPrimitive Sampler V2 wire
	# format. isa_circuit must already be transpiled to the target gate set
	# (i.e. produced by compile_circuit). When param_values is supplied it is
	# passed to SamplerPub.coerce() as a tuple so it is stored as a
	# BindingsArray alongside the symbolic circuit, which correctly populates
	# the param_array in the wire format.
	try:
		from qiskit.primitives.containers.sampler_pub import SamplerPub
		from qiskit import qasm3
	except Exception as exc:
		raise DEFwExecutionError(
			f"failed to import qiskit primitives: {exc}") from exc

	try:
		if param_values is not None:
			# The transpiler may optimise away parameters that existed in the
			# original circuit (e.g. rz(phi) just before a measurement at
			# optimization_level >= 2).  Drop any values whose parameter name
			# is no longer present in the ISA circuit so that SamplerPub.coerce
			# sees exactly the parameters it expects.
			isa_param_names = {p.name for p in isa_circuit.parameters}
			filtered = {k: v for k, v in param_values.items()
						if getattr(k, 'name', str(k)) in isa_param_names}
			removed = [k for k in param_values if getattr(k, 'name', str(k)) not in isa_param_names]
			if removed:
				logging.warning(
					"Parameters optimised away (removed) by the transpiler: %s",
					sorted(getattr(k, 'name', str(k)) for k in removed))
			pub = SamplerPub.coerce((isa_circuit, filtered), shots)
			logging.info(f"Circuit param_values used: {filtered}")
		else:
			pub = SamplerPub.coerce(isa_circuit, shots)
	except Exception as exc:
		raise DEFwExecutionError(f"Invalid parameters: {exc}") from exc

	qasm3_str = qasm3.dumps(
		pub.circuit,
		disable_constants=True,
		allow_aliasing=True,
		experimental=qasm3.ExperimentalFeatures.SWITCH_CASE_V1,
	)

	param_array = pub.parameter_values.as_array(pub.circuit.parameters).tolist()

	return json.dumps({
		"pubs": [(qasm3_str, param_array)],
		"version": 2,
		"support_qiskit": False,
		"shots": shots,
		"options": {},
	})
