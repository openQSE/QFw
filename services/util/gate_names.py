# Provider names for operations, mapped onto the names Qiskit's library uses.
# IQM calls its phased X rotation prx (Qiskit: r) and its reset reset_wait;
# Braket spells several standard gates its own way.
INSTRUCTION_ALIASES = {
	"prx": "r",
	"reset_wait": "reset",
	"cnot": "cx",
	"ccnot": "ccx",
	"toffoli": "ccx",
	"i": "id",
	"phaseshift": "p",
	"cphaseshift": "cp",
	"si": "sdg",
	"ti": "tdg",
	"v": "sx",
	"vi": "sxdg",
	"xx": "rxx",
	"yy": "ryy",
	"zz": "rzz",
}
