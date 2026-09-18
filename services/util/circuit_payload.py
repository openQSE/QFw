# The circuit a client submits for execution, in a format it names.
#
# The client sends the circuit in info["circuit"]:
#
#	{"format": "qpy", "data": "<base64 of the QPY bytes>"}
#	{"format": "openqasm2", "data": "OPENQASM 2.0; ..."}
#
# Binary formats travel as base64 text, so the info dict stays plain data on
# any transport. info["qasm"] is the older form. It is still read, as
# OpenQASM 2, when info["circuit"] is absent, so existing clients keep working.
#
# QPY is Qiskit's own serialization. It carries what OpenQASM 2 cannot, such
# as control flow, unbound parameters, gates like ecr, delays and circuit
# metadata. The QPMs that read circuits through Qiskit accept it. The
# simulator QPMs run OpenQASM 2 files and reject anything else by name.
#
# Each QPM should also list the formats it reads in its directory record, so a
# client can choose one before it submits. The DEFw directory accepts only a
# fixed catalog of QPM properties, so that waits on a catalog change there.

import base64
import binascii
import io

from defw_exception import DEFwExecutionError


OPENQASM2 = "openqasm2"
QPY = "qpy"
CIRCUIT_FORMATS = (OPENQASM2, QPY)


def circuit_payload(info):
	# Return (format, data) for the circuit in a circuit info dict. data is
	# text for OpenQASM 2 and bytes for QPY.
	info = info or {}
	circuit = info.get("circuit")
	if circuit is None:
		qasm = info.get("qasm")
		if not qasm:
			raise DEFwExecutionError(
				"circuit info carries no circuit. Send it in info['circuit'] "
				"with a format and data, or as OpenQASM 2 in info['qasm']")
		return OPENQASM2, qasm
	if not isinstance(circuit, dict):
		raise DEFwExecutionError(
			"info['circuit'] must be a mapping with a format and data")
	fmt = str(circuit.get("format") or "").strip().lower()
	if fmt not in CIRCUIT_FORMATS:
		raise DEFwExecutionError(
			f"unknown circuit format {fmt!r}. QFw reads "
			f"{', '.join(CIRCUIT_FORMATS)}")
	data = circuit.get("data")
	if not data or not isinstance(data, str):
		raise DEFwExecutionError(
			f"info['circuit'] carries no {fmt} data. The data is text, with "
			"binary formats base64 encoded")
	if fmt == QPY:
		try:
			return QPY, base64.b64decode(data, validate=True)
		except (binascii.Error, ValueError) as exc:
			raise DEFwExecutionError(
				f"the QPY circuit data is not valid base64: {exc}") from exc
	return fmt, data


def payload_bytes(info):
	# Return the submitted circuit as bytes, or None when there is none. The
	# scheduler keeps these bytes opaquely, in whatever format they came.
	info = info or {}
	if info.get("circuit") is None and not info.get("qasm"):
		return None
	_fmt, data = circuit_payload(info)
	return data if isinstance(data, bytes) else data.encode("utf-8")


def openqasm2_text(info):
	# Return the circuit as OpenQASM 2 text, for QPMs that run only that.
	fmt, data = circuit_payload(info)
	if fmt != OPENQASM2:
		raise DEFwExecutionError(
			f"this QPM runs OpenQASM 2 only and received a {fmt} circuit. "
			"Send it as OpenQASM 2")
	return data


def qiskit_input(info):
	# Return the circuit for a transcoder that reads circuits with Qiskit.
	# OpenQASM 2 stays text, so the existing parse and its manual fallback run
	# exactly as before. QPY is loaded into a QuantumCircuit.
	fmt, data = circuit_payload(info)
	if fmt == QPY:
		return load_qpy(data)
	return data


def load_qpy(data):
	# Load the single QuantumCircuit held in QPY bytes.
	try:
		from qiskit import qpy
	except Exception as exc:
		raise DEFwExecutionError(
			f"reading a QPY circuit needs qiskit: {exc}") from exc
	try:
		circuits = qpy.load(io.BytesIO(data))
	except Exception as exc:
		raise DEFwExecutionError(
			"could not load the QPY circuit. This QPM reads QPY up to "
			f"version {qpy.QPY_VERSION}: {exc}") from exc
	if len(circuits) != 1:
		raise DEFwExecutionError(
			"a QPY circuit payload holds exactly one circuit, "
			f"not {len(circuits)}")
	return circuits[0]
