from collections import OrderedDict
import sys

import numpy as np
import pytest

pytest.importorskip("qiskit_aer")
pytest.importorskip("qiskit_ibm_runtime")

from qiskit import QuantumCircuit, transpile  # noqa: E402
from qiskit.circuit.library import PauliEvolutionGate  # noqa: E402
from qiskit.quantum_info import SparsePauliOp, Statevector  # noqa: E402
from qiskit_aer import AerSimulator  # noqa: E402
from qiskit_ibm_runtime.fake_provider import FakeFez  # noqa: E402

from util.proxy_sampler import ideal_sources  # noqa: E402
from util.proxy_sampler.counts import counts_of  # noqa: E402
from util.proxy_sampler.errors import AerMemoryRefused, FfsimMemoryRefused  # noqa: E402
from util.proxy_sampler.ideal_sources import (  # noqa: E402
	AerStatevectors, IdealDraw, measured_map, on_active_qubits)
from util.proxy_sampler.rotation_states import (  # noqa: E402
	aer_result, aer_statevector, rotation_circuit, rotation_program)
from util.proxy_sampler.sampler import ProxySampler  # noqa: E402

MEMORY_MB = 4096
SHOTS = 20000


@pytest.fixture(scope="module")
def device():
	return FakeFez()


def _ghz(width):
	circuit = QuantumCircuit(width, width)
	circuit.h(0)
	for qubit in range(width - 1):
		circuit.cx(qubit, qubit + 1)
	circuit.measure(range(width), range(width))
	return circuit


def _probabilities(counts):
	total = sum(counts.values())
	return {word: count / total for word, count in counts.items()}


def test_proxy_agrees_with_aer_noisy_simulation_on_a_small_circuit(device):
	rng = np.random.default_rng(0)
	width = 6
	circuit = QuantumCircuit(width)
	for _ in range(3):
		for qubit in range(width):
			circuit.ry(rng.uniform(0, 3), qubit)
			circuit.rz(rng.uniform(0, 3), qubit)
		for qubit in range(width - 1):
			circuit.cx(qubit, qubit + 1)
	circuit.measure_all()
	isa = transpile(circuit, device, initial_layout=list(range(width)), seed_transpiler=1)

	sampler = ProxySampler(device, max_memory_mb=MEMORY_MB)
	bits, _ = sampler.sample(isa, SHOTS, np.random.default_rng(1))
	proxy = _probabilities(counts_of(bits))
	exact = _probabilities(
		AerSimulator.from_backend(device).run(isa, shots=SHOTS, seed_simulator=2).result().get_counts())

	def z_parity(distribution, qubits):
		return sum(p * (-1) ** sum(int(word[-1 - q]) for q in qubits) for word, p in distribution.items())

	single_qubits = [(q,) for q in range(width)]
	neighbouring_pairs = [(q, q + 1) for q in range(width - 1)]
	four_standard_errors = 4 * np.sqrt(2 / SHOTS)
	for qubits in single_qubits + neighbouring_pairs:
		difference = abs(z_parity(proxy, qubits) - z_parity(exact, qubits))
		assert difference < four_standard_errors + 0.01, qubits


def test_the_same_seed_draws_the_same_bits_and_another_seed_does_not(device):
	isa = transpile(_ghz(4), device, initial_layout=[0, 1, 2, 3], seed_transpiler=1)
	sampler = ProxySampler(device, max_memory_mb=MEMORY_MB)

	def counts(seed):
		return counts_of(sampler.sample(isa, 2000, np.random.default_rng(seed))[0])

	assert counts(5) == counts(5)
	assert counts(5) != counts(6)


def test_a_wide_clifford_circuit_samples_within_the_memory_limit(device):
	width = 100
	isa = transpile(_ghz(width), device, seed_transpiler=1, optimization_level=1)
	sampler = ProxySampler(device, max_memory_mb=MEMORY_MB)
	bits, diagnostics = sampler.sample(isa, 200, np.random.default_rng(3))
	assert bits.shape == (200, width)
	assert diagnostics["ideal"] == "matrix_product_state"
	assert 0 < diagnostics["fidelity"] < 1
	assert diagnostics["gates_2q"] >= width - 1


def test_diagnostics_report_the_error_budget_and_the_ideal_source(device):
	isa = transpile(_ghz(3), device, initial_layout=[0, 1, 2], seed_transpiler=1)
	_, diagnostics = ProxySampler(device, max_memory_mb=MEMORY_MB).sample(isa, 10, np.random.default_rng(0))
	assert {"tier", "ideal", "fidelity", "events_per_shot", "gates_2q", "gates_1q"} <= set(diagnostics)
	assert diagnostics["tier"] == "proxy"
	assert diagnostics["ideal"] == "statevector"
	assert diagnostics["gates_2q"] == 2


def test_the_logical_circuit_is_the_source_of_pauli_rotation_outcomes(device):
	logical = QuantumCircuit(3)
	logical.x(0)
	logical.append(PauliEvolutionGate(SparsePauliOp("XXI"), time=0.3), [0, 1, 2])
	logical.measure_all()
	isa = transpile(logical, device, initial_layout=[0, 1, 2], seed_transpiler=1)
	_, diagnostics = ProxySampler(device, max_memory_mb=MEMORY_MB).sample(
		isa, 10, np.random.default_rng(0), logical=logical)
	assert diagnostics["ideal"] == "pauli-rotations"


def test_a_pauli_rotation_program_is_exact():
	rng = np.random.default_rng(5)
	n = 6
	circuit = QuantumCircuit(n)
	for q in (0, 1, 4):
		circuit.x(q)
	for _ in range(12):
		label = "".join(rng.choice(list("IXYZ"), n))
		if set(label) != {"I"}:
			circuit.append(PauliEvolutionGate(SparsePauliOp(label), time=float(rng.normal(0, 0.4))), range(n))
	vector = aer_statevector(rotation_circuit(n, rotation_program(circuit)), MEMORY_MB)
	assert abs(np.vdot(Statevector(circuit.decompose(reps=4)).data, vector)) ** 2 > 1 - 1e-10

	hadamard = QuantumCircuit(2)
	hadamard.h(0)
	assert rotation_program(hadamard) is None


def test_aer_refuses_a_statevector_beyond_its_memory_by_name():
	with pytest.raises(AerMemoryRefused, match=r"Aer statevector simulation reached .* Required memory: 256M"):
		aer_result(_ghz(24), "statevector", 8, shots=1)


def test_a_circuit_aer_refuses_as_a_statevector_is_sampled_by_mps():
	source = AerStatevectors(OrderedDict(), 2, 8, {"statevector": 0, "mps": 0, "prefix_hits": 0})
	bits, method = source.outcomes(IdealDraw.of(_ghz(24), None, 100, np.random.default_rng(1)))
	assert method == "matrix_product_state" and source.stats["mps"] == 1
	assert all(row.all() or not row.any() for row in bits)


def test_a_circuit_aer_refuses_by_mps_too_is_refused_by_name(monkeypatch):
	def refused(circuit, method, max_memory_mb, **options):
		raise AerMemoryRefused(f"Aer {method} simulation", max_memory_mb, "Insufficient memory")

	monkeypatch.setattr(ideal_sources, "aer_result", refused)
	monkeypatch.setattr(ideal_sources, "aer_statevector", lambda circuit, max_memory_mb: refused(
		circuit, "statevector", max_memory_mb))
	source = AerStatevectors(OrderedDict(), 2, 8, {"statevector": 0, "mps": 0, "prefix_hits": 0})
	with pytest.raises(AerMemoryRefused, match="Aer matrix_product_state simulation"):
		source.outcomes(IdealDraw.of(_ghz(4), None, 10, np.random.default_rng(1)))


def test_a_circuit_with_no_active_qubit_is_its_global_phase():
	assert np.allclose(aer_statevector(QuantumCircuit(0, global_phase=0.5), 8), [np.exp(0.5j)])


def test_spinless_ffsim_circuit_samples_its_occupied_orbitals(device):
	ffsim = pytest.importorskip("ffsim")
	from qiskit import QuantumRegister

	register = QuantumRegister(3)
	logical = QuantumCircuit(register)
	logical.append(ffsim.qiskit.PrepareHartreeFockSpinlessJW(3, 2), register)
	logical.measure_all()
	isa = transpile(logical, device, initial_layout=[0, 1, 2], seed_transpiler=1)
	bits, diagnostics = ProxySampler(device, max_memory_mb=MEMORY_MB).sample(
		isa, 50, np.random.default_rng(0), logical=logical)
	assert diagnostics["ideal"] == "ffsim"
	assert (bits.mean(axis=0).round() == [1, 1, 0]).all()


def _hartree_fock(norb, nelec):
	ffsim = pytest.importorskip("ffsim")
	from qiskit import QuantumRegister

	register = QuantumRegister(2 * norb)
	logical = QuantumCircuit(register)
	logical.append(ffsim.qiskit.PrepareHartreeFockJW(norb, nelec), register)
	logical.measure_all()
	return logical


def _ffsim_source(max_memory_mb):
	return ideal_sources.FfsimStates(ideal_sources.IdealSource(None), OrderedDict(), 2, max_memory_mb, {"ffsim": 0})


def _draw(logical):
	return IdealDraw.of(logical, logical, 10, np.random.default_rng(0))


def test_an_ffsim_state_beyond_the_memory_limit_is_refused_by_name():
	with pytest.raises(FfsimMemoryRefused, match=r"12 orbitals and \(6, 6\) electrons needs 14 MiB, over its 8 MiB"):
		_ffsim_source(8).outcomes(_draw(_hartree_fock(12, (6, 6))))


def test_cached_ffsim_states_make_room_for_a_new_one_within_the_memory_limit():
	source = _ffsim_source(20)
	source.outcomes(_draw(_hartree_fock(12, (6, 6))))
	source.outcomes(_draw(_hartree_fock(12, (5, 7))))
	assert [state.nelec for state in source.states.values()] == [(5, 7)]


def test_sample_bases_measures_one_state_in_several_bases(device):
	prepared = QuantumCircuit(2)
	prepared.h(0)
	prepared.cx(0, 1)
	isa = transpile(prepared, device, initial_layout=[0, 1], seed_transpiler=1)
	sampler = ProxySampler(device, max_memory_mb=MEMORY_MB)
	records = sampler.sample_bases(isa, [0, 1], ["ZZ", "XX", "YY"], [4000, 4000, 4000], np.random.default_rng(0))

	assert [bits.shape for bits, _ in records] == [(4000, 2)] * 3
	zz, xx, yy = (bits for bits, _ in records)

	def agreement(bits):
		return (bits[:, 0] == bits[:, 1]).mean()

	assert agreement(zz) > 0.9
	assert agreement(xx) > 0.9
	assert 1 - agreement(yy) > 0.9
	assert all(diagnostics["ideal"] == "statevector-bases" for _, diagnostics in records)


def test_measured_map_and_active_qubits_ignore_idle_wires():
	circuit = QuantumCircuit(5, 2)
	circuit.h(3)
	circuit.measure(3, 1)
	assert measured_map(circuit) == [(3, 1)]
	compact, active = on_active_qubits(circuit)
	assert active == [3] and compact.num_qubits == 1


def _proxy_matched_pauli_noise(device, isa):
	from qiskit_aer.noise import NoiseModel, ReadoutError, pauli_error

	from util.proxy_sampler.calibration import PROCESS_PER_AVERAGE

	model = NoiseModel(basis_gates=device.configuration().basis_gates)
	used = {(i.operation.name, tuple(isa.find_bit(q).index for q in i.qubits)) for i in isa.data}
	for name, qargs in used:
		properties = device.target[name].get(qargs) if name in ("sx", "x", "cz") else None
		if properties is None or not properties.error:
			continue
		k = len(qargs)
		probability = PROCESS_PER_AVERAGE[k] * properties.error
		paulis = ["".join("IXYZ"[i] for i in index) for index in np.ndindex(*(4,) * k)][1:]
		model.add_quantum_error(pauli_error(
			[("I" * k, 1 - probability)] + [(p, probability / len(paulis)) for p in paulis]), name, list(qargs))
	readout = device.properties()
	for qubit in {isa.find_bit(i.qubits[0]).index for i in isa.data if i.operation.name == "measure"}:
		p10 = readout.qubit_property(qubit, "prob_meas1_prep0")[0]
		p01 = readout.qubit_property(qubit, "prob_meas0_prep1")[0]
		model.add_readout_error(ReadoutError([[1 - p10, p10], [p01, 1 - p01]]), [qubit])
	return model


def _mirror(width, rounds):
	forward = QuantumCircuit(width)
	forward.h(0)
	for qubit in range(width - 1):
		forward.cx(qubit, qubit + 1)
	forward.s(range(width))
	forward.h(range(width))
	circuit = QuantumCircuit(width, width)
	for _ in range(rounds):
		circuit.compose(forward, inplace=True)
		circuit.barrier()
		circuit.compose(forward.inverse(), inplace=True)
		circuit.barrier()
	circuit.measure(range(width), range(width))
	return circuit


def test_a_clifford_circuit_follows_its_noisy_distribution_under_the_proxy_noise_model(device):
	width = 4
	isa = transpile(_mirror(width, rounds=2), device, initial_layout=list(range(width)),
					seed_transpiler=1, optimization_level=0)
	density_matrix_shots = 400_000
	exact = _probabilities(AerSimulator(
		method="density_matrix", noise_model=_proxy_matched_pauli_noise(device, isa)).run(
		isa, shots=density_matrix_shots, seed_simulator=3).result().get_counts())

	bits, _ = ProxySampler(device, max_memory_mb=MEMORY_MB).sample(isa, SHOTS, np.random.default_rng(4))
	proxy = _probabilities(counts_of(bits))

	total_variation = 0.5 * sum(abs(proxy.get(k, 0) - exact.get(k, 0)) for k in set(proxy) | set(exact))
	assert total_variation < 0.01


def _through_qpy(circuit):
	import io

	from qiskit import qpy

	buffer = io.BytesIO()
	qpy.dump(circuit, buffer)
	return qpy.load(io.BytesIO(buffer.getvalue()))[0]


@pytest.mark.parametrize("spin", ["balanced", "unbalanced", "spinless"])
def test_an_ffsim_circuit_loaded_from_qpy_rebuilds_the_same_state(spin):
	ffsim = pytest.importorskip("ffsim")
	from util.proxy_sampler.fermionic import ffsim_program

	norb = 5
	if spin == "spinless":
		logical = QuantumCircuit(norb)
		logical.append(ffsim.qiskit.PrepareHartreeFockSpinlessJW(norb, 2), range(norb))
		operator = ffsim.qiskit.UCJOpSpinlessJW(ffsim.random.random_ucj_op_spinless(norb, n_reps=2, seed=3))
	else:
		logical = QuantumCircuit(2 * norb)
		logical.append(ffsim.qiskit.PrepareHartreeFockJW(norb, (3, 2)), range(2 * norb))
		random_operator = (ffsim.random.random_ucj_op_spin_balanced if spin == "balanced"
						   else ffsim.random.random_ucj_op_spin_unbalanced)
		operator_gate = (ffsim.qiskit.UCJOpSpinBalancedJW if spin == "balanced" else ffsim.qiskit.UCJOpSpinUnbalancedJW)
		operator = operator_gate(random_operator(norb, n_reps=2, with_final_orbital_rotation=True, seed=3))
	logical.append(operator, logical.qubits)
	native = ffsim.qiskit.final_state_vector(logical)

	program = ffsim_program(_through_qpy(logical))
	rebuilt = ffsim.qiskit.final_state_vector(program.circuit, norb=program.space.norb, nelec=program.space.nelec)

	assert program.space.nelec == native.nelec
	assert abs(np.vdot(native.vec, rebuilt.vec)) == pytest.approx(1, abs=1e-10)


def test_a_qpm_without_ffsim_samples_an_ffsim_circuit_with_the_next_source(device, monkeypatch):
	logical = _through_qpy(_hartree_fock(3, (1, 1)))
	isa = transpile(logical, device, initial_layout=list(range(6)), seed_transpiler=1)
	monkeypatch.setitem(sys.modules, "ffsim", None)

	_, diagnostics = ProxySampler(device, max_memory_mb=MEMORY_MB).sample(
		isa, 10, np.random.default_rng(0), logical=logical)

	assert diagnostics["ideal"] != "ffsim"


def _trotter_step(angle):
	step = QuantumCircuit(2, name="trotter")
	step.rxx(angle, 0, 1)
	logical = QuantumCircuit(2)
	logical.append(step.to_gate(), [0, 1])
	logical.measure_all()
	return logical


def test_composite_gates_with_one_name_and_different_angles_do_not_share_cached_outcomes(device):
	sampler = ProxySampler(device, max_memory_mb=MEMORY_MB)
	flips_both, flips_neither = np.pi, 2 * np.pi

	def likeliest(angle):
		logical = _trotter_step(angle)
		isa = transpile(logical, device, initial_layout=[0, 1], seed_transpiler=1, optimization_level=0)
		bits, diagnostics = sampler.sample(isa, 200, np.random.default_rng(0), logical=logical)
		assert diagnostics["ideal"] == "pauli-rotations"
		counts = counts_of(bits)
		return max(counts, key=counts.get)

	assert likeliest(flips_both) == "11"
	assert likeliest(flips_neither) == "00"


def _fully_depolarizing_delay():
	from util.proxy_sampler.calibration import Calibration

	no_coherence_times = Calibration({}, {}, {}, {}, None)
	circuit = QuantumCircuit(1, 1)
	circuit.delay(100, 0, unit="ns")
	circuit.measure(0, 0)
	return circuit, no_coherence_times


def test_a_fully_depolarizing_delay_flips_a_measured_bit_half_the_time():
	from util.proxy_sampler.pauli_frames import Program

	circuit, no_coherence_times = _fully_depolarizing_delay()
	shots = 40_000

	flips = Program.from_circuit(circuit, no_coherence_times).propagate(shots, np.random.default_rng(0))

	four_standard_errors = 4 * np.sqrt(0.25 / shots)
	assert abs(flips.mean() - 0.5) < four_standard_errors


def test_the_fidelity_is_the_chance_that_no_location_errs():
	from util.proxy_sampler.calibration import error_budget
	from util.proxy_sampler.coherence import MAXIMUM_IDLE_ERROR

	budget = error_budget(*_fully_depolarizing_delay())

	assert budget["fidelity"] == pytest.approx(1 - MAXIMUM_IDLE_ERROR)


def _measured_twice_into_one_bit(first_qubit_gate):
	circuit = QuantumCircuit(2, 1)
	first_qubit_gate(circuit)
	circuit.measure(0, 0)
	circuit.measure(1, 0)
	return circuit


def test_the_last_qubit_measured_into_a_bit_decides_its_ideal_value(device):
	isa = transpile(_measured_twice_into_one_bit(lambda circuit: circuit.x(0)), device, initial_layout=[0, 1],
					seed_transpiler=1)

	bits, _ = ProxySampler(device, max_memory_mb=MEMORY_MB).sample(isa, 2000, np.random.default_rng(0))

	assert bits.mean() < 0.1


def test_the_last_qubit_measured_into_a_bit_decides_its_noise():
	from util.proxy_sampler.calibration import Calibration
	from util.proxy_sampler.pauli_frames import Program

	circuit = _measured_twice_into_one_bit(lambda circuit: circuit.delay(100, 0, unit="ns"))

	flips = Program.from_circuit(circuit, Calibration({}, {}, {}, {}, None)).propagate(2000, np.random.default_rng(0))

	assert not flips.any()


def test_each_u3_moves_the_frame_by_its_own_angles():
	from qiskit.circuit.library import U3Gate

	from util.proxy_sampler.calibration import Calibration
	from util.proxy_sampler.coherence import Coherence
	from util.proxy_sampler.pauli_frames import Program

	pure_dephasing = Coherence(t1=1.0, t2=1e-12)
	calibration = Calibration({("u3", (0,)): 0.0}, {}, {0: pure_dephasing}, {}, None)
	circuit = QuantumCircuit(1, 1)
	circuit.delay(100, 0, unit="ns")
	circuit.append(U3Gate(np.pi, 0, np.pi), [0])
	circuit.append(U3Gate(np.pi / 2, 0, np.pi), [0])
	circuit.measure(0, 0)
	shots = 40_000

	flips = Program.from_circuit(circuit, calibration).propagate(shots, np.random.default_rng(0))

	z_error_turned_into_x_by_the_hadamard = 0.5
	assert abs(flips.mean() - z_error_turned_into_x_by_the_hadamard) < 4 * np.sqrt(0.25 / shots)
