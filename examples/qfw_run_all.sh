#!/bin/bash

set -uo pipefail

usage() {
	cat <<EOF
Usage: ./qfw_run_all.sh [options]

Run backend-compatible QFw examples sequentially. In local mode each wrapper
owns its QPM lifecycle. In site mode every wrapper connects to the existing
site directory and QPM while owning only its application runtime.

Options:
  --service-mode MODE       local or site (default: local)
  --backend NAME            Backend for compatible examples (default: nwqsim)
  --site-config PATH        Override the activated site configuration
  --runtime-config PATH     Override the selected runtime profile
  --tests LIST              Comma-separated case names (default: all)
  --verbose                 Enable shell tracing in example wrappers
  -h, --help                Show this help

Environment overrides:
  QFW_RUN_ALL_SERVICE_MODE=<local|site>
  QFW_RUN_ALL_BACKEND=<name>          Backend for compatible tests
  QFW_RUN_ALL_SITE_CONFIG=<path>      Site configuration
  QFW_RUN_ALL_RUNTIME_CONFIG=<path>   Runtime configuration override
  QFW_RUN_ALL_TESTS=<list>            Comma-separated case names
  QFW_RUN_ALL_QUBITS=<n>              Qubit count for GHZ/simple tests
  QFW_RUN_ALL_ITERS=<n>               Iterations for GHZ tests
  QFW_RUN_ALL_SHOTS=<n>               Shots for SupermarQ
  QFW_RUN_ALL_VQE_ITERS=<n>           Optimizer iterations for VQE
  QFW_RUN_ALL_SHIM_LIB=<qrmi|qdmi>    Shim library for shim smoke test
  QFW_RUN_ALL_CHEM_APP=<script.py>    Optional chemistry app script
EOF
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

service_mode="${QFW_RUN_ALL_SERVICE_MODE:-local}"
backend="${QFW_RUN_ALL_BACKEND:-nwqsim}"
site_config="${QFW_RUN_ALL_SITE_CONFIG:-${QFW_SITE_CONFIG:-}}"
runtime_config="${QFW_RUN_ALL_RUNTIME_CONFIG:-${QFW_RUNTIME_CONFIG:-}}"
selected_tests="${QFW_RUN_ALL_TESTS:-all}"
verbose="${QFW_EXAMPLE_VERBOSE:-no}"

qfw_run_all_need_value() {
	if [[ $# -lt 2 || -z "${2:-}" ]]; then
		echo "ERROR: $1 requires a value" >&2
		exit 2
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--service-mode)
			qfw_run_all_need_value "$@"
			service_mode="$2"
			shift 2
			;;
		--backend)
			qfw_run_all_need_value "$@"
			backend="$2"
			shift 2
			;;
		--site-config)
			qfw_run_all_need_value "$@"
			site_config="$2"
			shift 2
			;;
		--runtime-config)
			qfw_run_all_need_value "$@"
			runtime_config="$2"
			shift 2
			;;
		--tests)
			qfw_run_all_need_value "$@"
			selected_tests="$2"
			shift 2
			;;
		--verbose)
			verbose="yes"
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "ERROR: unknown option: $1" >&2
			usage >&2
			exit 2
			;;
	esac
done

case "${service_mode}" in
	local|site) ;;
	*)
		echo "ERROR: --service-mode must be local or site: ${service_mode}" >&2
		exit 2
		;;
esac
if [[ "${service_mode}" != "site" && "${backend}" == "iqm" ]]; then
	echo "ERROR: the IQM backend requires --service-mode site" >&2
	exit 2
fi

if [[ "${selected_tests}" != "all" ]]; then
	IFS=',' read -r -a requested_cases <<<"${selected_tests}"
	for requested_case in "${requested_cases[@]}"; do
		case "${requested_case}" in
			init-test|shim-smoke|qiskit-simple|ghz-qiskit|ghz-pennylane|\
			pennylane|qaoa|qiskit-vqe|supermarq|chemistry) ;;
			*)
				echo "ERROR: unknown --tests case: ${requested_case:-<empty>}" >&2
				exit 2
				;;
		esac
	done
fi

examples_dir="$(qfw_example_examples_dir)"
if [[ ! -d "${examples_dir}" ]]; then
	echo "ERROR: examples directory not found: ${examples_dir}" >&2
	exit 1
fi

qfw_example_require_runtime || exit $?

qubits="${QFW_RUN_ALL_QUBITS:-4}"
iterations="${QFW_RUN_ALL_ITERS:-1}"
shots="${QFW_RUN_ALL_SHOTS:-128}"
vqe_iterations="${QFW_RUN_ALL_VQE_ITERS:-1}"
shim_lib="${QFW_RUN_ALL_SHIM_LIB:-qrmi}"
timestamp="$(date +%Y%m%d-%H%M%S)"
log_root="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}}/examples-run-${timestamp}"

mkdir -p "${log_root}"

declare -a test_names=()
declare -a test_rcs=()
declare -a test_logs=()
declare -a test_results=()
declare -a test_statuses=()
declare -a test_reasons=()
test_index=0

common_args=(--service-mode "${service_mode}" --backend "${backend}")
if [[ "${service_mode}" == "site" ]]; then
	if [[ -n "${site_config}" ]]; then
		common_args+=(--site-config "${site_config}")
	fi
	if [[ -n "${runtime_config}" ]]; then
		common_args+=(--runtime-config "${runtime_config}")
	fi
fi
case "${verbose}" in
	1|yes|true|on|y|YES|TRUE|ON|Y) common_args=(--verbose "${common_args[@]}") ;;
esac

qfw_run_all_case_selected() {
	local name="$1"
	if [[ "${selected_tests}" == "all" ]]; then
		return 0
	fi
	case ",${selected_tests}," in
		*",${name},"*) return 0 ;;
		*) return 1 ;;
	esac
}

skip_case() {
	local name="$1"
	local reason="$2"
	if ! qfw_run_all_case_selected "${name}"; then
		return 0
	fi
	test_index=$((test_index + 1))
	test_names+=("${name}")
	test_rcs+=("0")
	test_logs+=("")
	test_results+=("")
	test_statuses+=("skipped")
	test_reasons+=("${reason}")
	echo "[$test_index] ${name}: SKIP (${reason})"
}

run_case() {
	local name="$1"
	shift
	if ! qfw_run_all_case_selected "${name}"; then
		return 0
	fi

	test_index=$((test_index + 1))
	local log_file
	local result_file
	printf -v log_file "%s/%02d-%s.log" "${log_root}" "${test_index}" "${name}"
	printf -v result_file "%s/%02d-%s.jsonl" "${log_root}" "${test_index}" "${name}"

	echo "[$test_index] ${name}: $*"
	(
		cd "${examples_dir}" || exit 1
		QFW_EXAMPLE_RESULT_FILE="${result_file}" "$@"
	) >"${log_file}" 2>&1
	local rc=$?
	local reason=""
	if [[ ${rc} -eq 0 ]] &&
	   ! qfw_example_result_is_terminal_success "${result_file}"; then
		rc=1
		reason="missing or unsuccessful terminal wrapper result"
		echo "ERROR: ${reason}" >>"${log_file}"
	fi

	test_names+=("${name}")
	test_rcs+=("${rc}")
	test_logs+=("${log_file}")
	test_results+=("${result_file}")
	test_reasons+=("${reason}")

	if [[ ${rc} -eq 0 ]]; then
		test_statuses+=("passed")
		echo "[$test_index] ${name}: PASS (${log_file})"
	else
		test_statuses+=("failed")
		echo "[$test_index] ${name}: FAIL rc=${rc} (${log_file})"
	fi
}

run_case init-test ./qfw_init_test.sh "${common_args[@]}"
if [[ "${service_mode}" == "local" ]]; then
	run_case shim-smoke ./qfw_shim_smoke.sh --lib "${shim_lib}"
else
	skip_case shim-smoke "shim smoke owns its specialized local service"
fi
run_case qiskit-simple ./qfw_qiskit_simple.sh "${common_args[@]}" "${qubits}"
run_case ghz-qiskit ./qfw_ghz.sh "${common_args[@]}" \
	qiskit "${qubits}" "${backend}" "${iterations}"
run_case ghz-pennylane ./qfw_ghz.sh "${common_args[@]}" \
	pennylane "${qubits}" "${backend}" \
	"${iterations}"
run_case pennylane ./qfw_pennylane.sh "${common_args[@]}"
run_case qaoa ./qfw_qaoa.sh "${common_args[@]}" "${backend}"
if [[ "${backend}" == "nwqsim" ]]; then
	run_case qiskit-vqe ./qfw_qiskit_vqe.sh "${common_args[@]}" \
		"${vqe_iterations}"
else
	skip_case qiskit-vqe "requires a statevector backend"
fi
run_case supermarq ./qfw_supermarq.sh "${common_args[@]}" \
	sync 1 "${qubits}" "${shots}" false ghz "${backend}"

if [[ -n "${QFW_RUN_ALL_CHEM_APP:-}" ]]; then
	run_case chemistry ./qfw_chem_app.sh \
		"${common_args[@]}" "${QFW_RUN_ALL_CHEM_APP}"
else
	skip_case chemistry "requires QFW_RUN_ALL_CHEM_APP"
fi

echo
echo "QFw example summary"
echo "Logs: ${log_root}"
summary_file="${log_root}/summary.jsonl"
: >"${summary_file}"

failed=0
for i in "${!test_names[@]}"; do
	name="${test_names[$i]}"
	rc="${test_rcs[$i]}"
	log_file="${test_logs[$i]}"
	result_file="${test_results[$i]}"
	status="${test_statuses[$i]}"
	reason="${test_reasons[$i]}"
	if [[ -f "${result_file}" ]]; then
		cat "${result_file}" >>"${summary_file}"
	fi
	if [[ "${status}" == "skipped" ]]; then
		printf "SKIP %-18s reason=%s\n" "${name}" "${reason}"
		python3 - "${name}" "${backend}" "${service_mode}" \
			"${reason}" >>"${summary_file}" <<'PY'
import json
import sys
import time

name, backend, service_mode, reason = sys.argv[1:]
print(json.dumps({
	"schema": "qfw-run-all-case-v1",
	"kind": "suite-case",
	"name": name,
	"backend": backend,
	"service_mode": service_mode,
	"status": "skipped",
	"reason": reason,
	"timestamp_ns": time.time_ns(),
}, sort_keys=True))
PY
	elif [[ ${rc} -eq 0 ]]; then
		printf "PASS  %-18s log=%s result=%s\n" \
			"${name}" "${log_file}" "${result_file}"
	else
		printf "FAIL  %-18s rc=%s reason=%s log=%s result=%s\n" \
			"${name}" "${rc}" "${reason}" "${log_file}" "${result_file}"
		failed=1
	fi
done
echo "Summary JSONL: ${summary_file}"

exit "${failed}"
