from qiskit.circuit import Measure, Reset
from qiskit.circuit.library import (
	CCXGate,
	CHGate,
	CSGate,
	CSXGate,
	CSdgGate,
	CSwapGate,
	CUGate,
	CXGate,
	CYGate,
	CZGate,
	CPhaseGate,
	CRXGate,
	CRYGate,
	CRZGate,
	HGate,
	IGate,
	PhaseGate,
	RXGate,
	RXXGate,
	RYGate,
	RYYGate,
	RZGate,
	RZZGate,
	SGate,
	SXGate,
	SXdgGate,
	SdgGate,
	SwapGate,
	TGate,
	TdgGate,
	U1Gate,
	U2Gate,
	U3Gate,
	UGate,
	XGate,
	YGate,
	ZGate,
)
from qiskit.transpiler import Target


QFW_NUM_QUBITS = 400


def qfw_basis_gates():
	return [
		"x", "y", "z", "h", "s", "sdg", "t", "tdg", "rx", "ry",
		"rz", "sx", "sxdg", "p", "u", "cx", "cy", "cz", "ch", "cs",
		"csdg", "crx", "cry", "crz", "csx", "cp", "cu", "id", "swap",
		"measure", "reset", "u1", "u2", "u3", "ccx", "cswap", "rxx",
		"ryy", "rzz",
	]


def build_qfw_target(num_qubits=QFW_NUM_QUBITS):
	target = Target(
		description="Quantum Framework (QFw) Target",
		num_qubits=num_qubits,
	)

	for instruction in [
		IGate(),
		XGate(),
		YGate(),
		ZGate(),
		HGate(),
		SGate(),
		SdgGate(),
		TGate(),
		TdgGate(),
		SXGate(),
		SXdgGate(),
		RXGate(0),
		RYGate(0),
		RZGate(0),
		PhaseGate(0),
		UGate(0, 0, 0),
		U1Gate(0),
		U2Gate(0, 0),
		U3Gate(0, 0, 0),
		CXGate(),
		CYGate(),
		CZGate(),
		CHGate(),
		CSGate(),
		CSdgGate(),
		CSXGate(),
		CPhaseGate(0),
		CUGate(0, 0, 0, 0),
		CRXGate(0),
		CRYGate(0),
		CRZGate(0),
		SwapGate(),
		RXXGate(0),
		RYYGate(0),
		RZZGate(0),
		CCXGate(),
		CSwapGate(),
		Measure(),
		Reset(),
	]:
		target.add_instruction(instruction)

	return target
