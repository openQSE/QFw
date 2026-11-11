from collections import OrderedDict

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
from util.proxy_sampler.errors import AerMemoryRefused  # noqa: E402
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
	assert diagnostics["ideal"] in ("pauli-rotations", "statevector")


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
