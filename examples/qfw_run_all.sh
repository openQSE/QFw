#!/bin/bash

set -uo pipefail

usage() {
	cat <<EOF
Usage: ./qfw_run_all.sh

Run the QFw example wrappers sequentially. Each wrapper owns its own
qfw_setup.sh and qfw_teardown.sh lifecycle.

Environment overrides:
  QFW_RUN_ALL_BACKEND=<nwqsim|tnqvm>  Backend for backend-selectable tests
  QFW_RUN_ALL_QUBITS=<n>              Qubit count for GHZ/simple tests
  QFW_RUN_ALL_ITERS=<n>               Iterations for GHZ tests
  QFW_RUN_ALL_SHOTS=<n>               Shots for SupermarQ
  QFW_RUN_ALL_VQE_ITERS=<n>           Optimizer iterations for VQE
  QFW_RUN_ALL_CHEM_APP=<script.py>    Optional chemistry app script
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
	usage
	exit 0
fi

if [[ -z "${QFW_PATH:-}" ]]; then
	echo "ERROR: QFW_PATH is not set. Source qfw_activate first." >&2
	exit 1
fi

examples_dir="${QFW_PATH}/examples"
if [[ ! -d "${examples_dir}" ]]; then
	echo "ERROR: examples directory not found: ${examples_dir}" >&2
	exit 1
fi

if ! command -v qfw_setup.sh >/dev/null 2>&1; then
	echo "ERROR: qfw_setup.sh is not in PATH. Source qfw_activate first." >&2
	exit 1
fi

backend="${QFW_RUN_ALL_BACKEND:-nwqsim}"
qubits="${QFW_RUN_ALL_QUBITS:-4}"
iterations="${QFW_RUN_ALL_ITERS:-1}"
shots="${QFW_RUN_ALL_SHOTS:-128}"
vqe_iterations="${QFW_RUN_ALL_VQE_ITERS:-1}"
timestamp="$(date +%Y%m%d-%H%M%S)"
log_root="${QFW_TMP_PATH:-/tmp}/examples-run-${timestamp}"

mkdir -p "${log_root}"

declare -a test_names=()
declare -a test_rcs=()
declare -a test_logs=()
test_index=0

run_case() {
	local name="$1"
	shift

	test_index=$((test_index + 1))
	local log_file
	printf -v log_file "%s/%02d-%s.log" "${log_root}" "${test_index}" "${name}"

	echo "[$test_index] ${name}: $*"
	(
		cd "${examples_dir}" || exit 1
		"$@"
	) >"${log_file}" 2>&1
	local rc=$?

	test_names+=("${name}")
	test_rcs+=("${rc}")
	test_logs+=("${log_file}")

	if [[ ${rc} -eq 0 ]]; then
		echo "[$test_index] ${name}: PASS (${log_file})"
	else
		echo "[$test_index] ${name}: FAIL rc=${rc} (${log_file})"
	fi
}

run_case init-test ./qfw_init_test.sh
run_case mpi-smoke ./qfw_mpi_smoke.sh
run_case qiskit-simple ./qfw_qiskit_simple.sh "${qubits}"
run_case ghz-qiskit ./qfw_ghz.sh qiskit "${qubits}" "${backend}" "${iterations}"
run_case ghz-pennylane ./qfw_ghz.sh pennylane "${qubits}" "${backend}" \
	"${iterations}"
run_case pennylane ./qfw_pennylane.sh
run_case qaoa ./qfw_qaoa.sh "${backend}"
run_case qiskit-vqe ./qfw_qiskit_vqe.sh "${vqe_iterations}"
run_case supermarq ./qfw_supermarq.sh sync 1 "${qubits}" "${shots}" false ghz \
	"${backend}"

if [[ -n "${QFW_RUN_ALL_CHEM_APP:-}" ]]; then
	run_case chemistry ./qfw_chem_app.sh "${QFW_RUN_ALL_CHEM_APP}"
fi

echo
echo "QFw example summary"
echo "Logs: ${log_root}"

failed=0
for i in "${!test_names[@]}"; do
	name="${test_names[$i]}"
	rc="${test_rcs[$i]}"
	log_file="${test_logs[$i]}"
	if [[ ${rc} -eq 0 ]]; then
		printf "PASS  %-18s %s\n" "${name}" "${log_file}"
	else
		printf "FAIL  %-18s rc=%s %s\n" "${name}" "${rc}" "${log_file}"
		failed=1
	fi
done

exit "${failed}"
