BARRIER_DELAY = frozenset({"barrier", "delay"})
CLIFFORD_1Q = frozenset({"sx", "sxdg", "x", "y", "z", "h", "s", "sdg", "id"})
VIRTUAL = frozenset({"rz", "delay", "barrier", "measure", "reset", "id"})
NOISELESS = frozenset({"barrier", "measure", "rz", "id", "delay", "reset", "p", "u1"})
GATES_DIGESTED_BY_PARAMETER_VALUE = frozenset({
	"rz", "sx", "sxdg", "x", "y", "z", "h", "s", "sdg", "t", "tdg", "cx", "cz", "ecr", "swap", "rx", "ry", "rzz",
	"rxx", "ryy", "p", "u", "u1", "u2", "u3", "measure", "barrier", "delay", "reset", "id", "iswap"})
FFSIM_GATES = frozenset({
	"hartree_fock_jw", "slater_jw", "ucj_balanced_jw", "ucj_unbalanced_jw", "orb_rot_jw", "diag_coulomb_jw",
	"num_op_sum_jw", "num_num_ansatz_jw", "givens_ansatz_jw", "ucj_jw", "ucj_op_spin_balanced_jw"})
