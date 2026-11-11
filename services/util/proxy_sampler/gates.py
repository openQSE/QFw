# Gate groups the calibrated noise model treats alike.

BARRIER_DELAY = frozenset({"barrier", "delay"})
CLIFFORD_1Q = frozenset({"sx", "sxdg", "x", "y", "z", "h", "s", "sdg", "id"})
# Instructions that cost no error budget.
VIRTUAL = frozenset({"rz", "delay", "barrier", "measure", "reset", "id"})
# Instructions that inject no gate error.
NOISELESS = frozenset({"barrier", "measure", "rz", "id", "delay", "reset", "p", "u1"})
# Gates whose parameters digest by value; any other gate digests by its attributes.
STANDARD = frozenset({
	"rz", "sx", "sxdg", "x", "y", "z", "h", "s", "sdg", "t", "tdg", "cx", "cz", "ecr", "swap", "rx", "ry", "rzz",
	"rxx", "ryy", "p", "u", "u1", "u2", "u3", "measure", "barrier", "delay", "reset", "id", "iswap"})
# Gates of ffsim's Jordan-Wigner circuits.
FFSIM_GATES = frozenset({
	"hartree_fock_jw", "slater_jw", "ucj_balanced_jw", "ucj_unbalanced_jw", "orb_rot_jw", "diag_coulomb_jw",
	"num_op_sum_jw", "num_num_ansatz_jw", "givens_ansatz_jw", "ucj_jw", "ucj_op_spin_balanced_jw"})
