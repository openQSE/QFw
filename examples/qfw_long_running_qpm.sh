#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

usage() {
	cat <<EOF
Usage: ./qfw_long_running_qpm.sh [options]

Start a site directory, PRTE DVM, and long-running QPM service, then run
concurrent application waves against that same service plane.

Options:
  --apps N                 Concurrent application count per wave (default: 2)
  --waves N                Number of concurrent application waves (default: 2)
  --backend NAME           Backend to use (default: nwqsim)
  --run MODE               SupermarQ run mode: sync or async (default: sync)
  --startqbit N            Starting qubit count (default: 4)
  --shots N                Shot count (default: 128)
  --increase BOOL          Increase qubits per iteration (default: false)
  --method NAME            SupermarQ method: ghz or vqe (default: ghz)
  --iterations N           SupermarQ iterations per app (default: 1)
  --timeout SEC            Service startup timeout (default: 120)
  --run-dir DIR            Directory for logs and generated configs
  --service-node NODE      Node for site dirsvc, PRTE DVM, and QPM
  --app-nodes NODELIST     Node list for application steps
  --allow-node-reuse       Allow app count to exceed distinct app nodes
  -h, --help               Show this help

The intended allocation has at least three nodes: one service node and two
application nodes. The service plane is started once and is not owned by any
application qfw-teardown.
EOF
}

apps=2
waves=2
backend="nwqsim"
run_mode="sync"
startqbit=4
shots=128
increase="false"
method="ghz"
supermarq_iterations=1
startup_timeout=120
run_dir=""
service_node_override="${QFW_LONG_RUNNING_QPM_SERVICE_NODE:-}"
app_nodes_override="${QFW_LONG_RUNNING_QPM_APP_NODES:-}"
allow_node_reuse="${QFW_LONG_RUNNING_QPM_ALLOW_NODE_REUSE:-no}"

qfw_lrq_need_value() {
	local option="${1:-option}"
	if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == --* ]]; then
		echo "ERROR: ${option} requires a value" >&2
		exit 2
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--apps)
			qfw_lrq_need_value "$@"
			apps="$2"
			shift 2
			;;
		--waves)
			qfw_lrq_need_value "$@"
			waves="$2"
			shift 2
			;;
		--backend)
			qfw_lrq_need_value "$@"
			backend="$2"
			shift 2
			;;
		--run)
			qfw_lrq_need_value "$@"
			run_mode="$2"
			shift 2
			;;
		--startqbit)
			qfw_lrq_need_value "$@"
			startqbit="$2"
			shift 2
			;;
		--shots)
			qfw_lrq_need_value "$@"
			shots="$2"
			shift 2
			;;
		--increase)
			qfw_lrq_need_value "$@"
			increase="$2"
			shift 2
			;;
		--method)
			qfw_lrq_need_value "$@"
			method="$2"
			shift 2
			;;
		--iterations)
			qfw_lrq_need_value "$@"
			supermarq_iterations="$2"
			shift 2
			;;
		--timeout)
			qfw_lrq_need_value "$@"
			startup_timeout="$2"
			shift 2
			;;
		--run-dir)
			qfw_lrq_need_value "$@"
			run_dir="$2"
			shift 2
			;;
		--service-node)
			qfw_lrq_need_value "$@"
			service_node_override="$2"
			shift 2
			;;
		--app-nodes)
			qfw_lrq_need_value "$@"
			app_nodes_override="$2"
			shift 2
			;;
		--allow-node-reuse)
			allow_node_reuse="yes"
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

qfw_lrq_bool_enabled() {
	case "${1:-}" in
		1|yes|true|on|y|YES|TRUE|ON|Y) return 0 ;;
		*) return 1 ;;
	esac
}

qfw_lrq_require_positive_int() {
	local name="$1"
	local value="$2"
	if ! [[ "${value}" =~ ^[0-9]+$ ]] || [[ "${value}" -le 0 ]]; then
		echo "ERROR: ${name} must be a positive integer: ${value}" >&2
		exit 2
	fi
}

qfw_lrq_require_runtime() {
	qfw_example_require_runtime
	local command_name
	for command_name in qfw-dirsvc-start qfw-service-start prte pterm; do
		if ! command -v "${command_name}" >/dev/null 2>&1; then
			echo "ERROR: ${command_name} is not in PATH" >&2
			return 1
		fi
	done
}

qfw_lrq_expand_nodelist() {
	local nodelist="$1"
	if [[ -z "${nodelist}" ]]; then
		return 0
	fi
	if command -v scontrol >/dev/null 2>&1; then
		scontrol show hostnames "${nodelist}"
		return 0
	fi
	python3 - "${nodelist}" <<'PY'
import sys

value = sys.argv[1]
try:
	from defw_util import expand_host_list
	for host in expand_host_list(value):
		print(host)
except Exception:
	for item in value.split(","):
		item = item.strip()
		if item:
			print(item)
PY
}

qfw_lrq_collect_allocation_nodes() {
	local raw_lists=()
	if [[ -n "${QFW_LONG_RUNNING_QPM_NODELIST:-}" ]]; then
		raw_lists+=("${QFW_LONG_RUNNING_QPM_NODELIST}")
	elif [[ -n "${QFW_GROUP_0_NODELIST:-}" || -n "${QFW_GROUP_1_NODELIST:-}" ]]; then
		raw_lists+=("${QFW_GROUP_0_NODELIST:-}" "${QFW_GROUP_1_NODELIST:-}")
	elif [[ -n "${SLURM_JOB_NODELIST_HET_GROUP_0:-}" ||
	        -n "${SLURM_JOB_NODELIST_HET_GROUP_1:-}" ]]; then
		raw_lists+=(
			"${SLURM_JOB_NODELIST_HET_GROUP_0:-}"
			"${SLURM_JOB_NODELIST_HET_GROUP_1:-}"
		)
	elif [[ -n "${SLURM_JOB_NODELIST:-}" ]]; then
		raw_lists+=("${SLURM_JOB_NODELIST}")
	else
		hostname
		return 0
	fi

	local raw
	for raw in "${raw_lists[@]}"; do
		qfw_lrq_expand_nodelist "${raw}"
	done | awk 'NF && !seen[$0]++'
}

qfw_lrq_service_runtime_config() {
	local candidate
	for candidate in \
		"${QFW_SERVICE_RUNTIME_CONFIG:-}" \
		"${QFW_SHARE_DIR:-}/config/services/service-runtime.yaml" \
		"${QFW_SHARE_DIR:-}/config/services/templates/container.yaml" \
		"${QFW_PREFIX:-}/services/config/container.yaml"; do
		if [[ -n "${candidate}" && -r "${candidate}" ]]; then
			printf "%s\n" "${candidate}"
			return 0
		fi
	done
	echo "ERROR: no service runtime config found" >&2
	return 1
}

qfw_lrq_on_node() {
	local node="$1"
	shift
	if [[ -n "${SLURM_JOB_ID:-}" &&
	      "${QFW_LONG_RUNNING_QPM_DISABLE_SRUN:-no}" != "yes" ]]; then
		srun --nodes=1 --ntasks=1 --nodelist "${node}" "$@"
	else
		"$@"
	fi
}

qfw_lrq_wait_for_file() {
	local path="$1"
	local timeout="$2"
	local label="$3"
	local start
	start="$(date +%s)"
	while [[ ! -s "${path}" ]]; do
		if (( "$(date +%s)" - start >= timeout )); then
			echo "ERROR: timed out waiting for ${label}: ${path}" >&2
			return 1
		fi
		sleep 1
	done
}

qfw_lrq_write_configs() {
	site_config="${run_dir}/config/site.yaml"
	runtime_config="${run_dir}/config/runtime-site.yaml"
	service_manifest="${run_dir}/config/long-running-qpm-services.yaml"
	mkdir -p "${run_dir}/config"
	cat >"${site_config}" <<EOF
install:
  qfw-prefix: ${QFW_PREFIX}
  defw-prefix: ${DEFW_PREFIX}

directory:
  site:
    name: qfw-site-dirsvc
    endpoint: ${service_node}:${dirsvc_port}
    connect-timeout-seconds: ${startup_timeout}
EOF
	cat >"${runtime_config}" <<EOF
resolver:
  scope-order:
    - site
EOF
	cat >"${service_manifest}" <<EOF
services:
  - name: ${backend}
    module: svc_${backend}_qpm
    load-modules: svc_${backend}_qpm,api_launcher
    agent-prefix: qpm_${backend}
    target: ${service_node}
    assigned-hosts: ${service_node}
    assigned-hosts-env: QFW_QPM_ASSIGNED_HOSTS
EOF
}

qfw_lrq_start_dirsvc() {
	echo "Starting site dirsvc on ${service_node}:${dirsvc_port}"
	qfw_lrq_on_node "${service_node}" \
		qfw-dirsvc-start \
			--background \
			--site-config "${site_config}" \
			--run-dir "${run_dir}" \
			--name qfw-site-dirsvc \
			--host "${service_node}" \
			--listen-port "${dirsvc_port}" \
			--timeout "${startup_timeout}" \
			--pid-file "${dirsvc_pid_file}" \
			--ready-file "${dirsvc_ready_file}"
}

qfw_lrq_start_dvm() {
	echo "Starting PRTE DVM on ${service_node}"
	mkdir -p "$(dirname "${dvm_uri}")"
	local command=(
		prte
		--host "${service_node}:*"
		--report-uri "${dvm_uri}"
	)
	if [[ -n "${SLURM_JOB_ID:-}" ]]; then
		command+=(
			-x "SLURM_JOB_ID=${SLURM_JOB_ID}"
			-x "SLURM_JOBID=${SLURM_JOB_ID}"
			--prtemca ras ^slurm
			--prtemca plm slurm
			--prtemca plm_slurm_verbose 100
			--prtemca plm_base_verbose 100
			--prtemca ras_base_verbose 100
		)
	fi
	if [[ "$(id -u)" == "0" ]]; then
		command+=(--allow-run-as-root)
	fi
	command+=(--daemonize)
	qfw_lrq_on_node "${service_node}" "${command[@]}"
	qfw_lrq_wait_for_file "${dvm_uri}" "${startup_timeout}" "PRTE DVM URI"
}

qfw_lrq_start_qpm() {
	echo "Starting long-running ${backend} QPM on ${service_node}"
	(
		export QFW_DVM_URI_PATH="${dvm_uri}"
		export QFW_QPM_ASSIGNED_HOSTS="${service_node}"
		export QFW_SITE_DIRSVC_ENDPOINTS="${service_node}:${dirsvc_port}"
		export QFW_SITE_DIRSVC_NAME="qfw-site-dirsvc"
		export QFW_SERVICE_RUNTIME_CONFIG="${service_runtime_config}"
		qfw_lrq_on_node "${service_node}" \
			qfw-service-start \
				--background \
				--run-dir "${run_dir}" \
				--service-id "${backend}" \
				--service-manifest "${service_manifest}" \
				--site-config "${site_config}" \
				--service-runtime-config "${service_runtime_config}" \
				--operation-mode long-running \
				--listen-port "${qpm_port}" \
				--telnet-port "${qpm_telnet_port}" \
				--timeout "${startup_timeout}" \
				--pid-file "${qpm_pid_file}" \
				--ready-file "${qpm_ready_file}"
	)
}

qfw_lrq_pid_alive() {
	local node="$1"
	local pid_file="$2"
	if [[ ! -r "${pid_file}" ]]; then
		return 1
	fi
	local pid
	pid="$(<"${pid_file}")"
	qfw_lrq_on_node "${node}" python3 - "${pid}" <<'PY'
import os
import sys

try:
	os.kill(int(sys.argv[1]), 0)
except OSError:
	sys.exit(1)
PY
}

qfw_lrq_kill_pid_file() {
	local node="$1"
	local pid_file="$2"
	if [[ ! -r "${pid_file}" ]]; then
		return 0
	fi
	local pid
	pid="$(<"${pid_file}")"
	echo "Stopping pid ${pid} on ${node}"
	qfw_lrq_on_node "${node}" python3 - "${pid}" <<'PY'
import os
import signal
import sys
import time

pid = int(sys.argv[1])

def alive():
	try:
		os.kill(pid, 0)
		return True
	except ProcessLookupError:
		return False
	except PermissionError:
		return True

def deliver(sig):
	delivered = False
	try:
		os.killpg(pid, sig)
		delivered = True
	except (ProcessLookupError, OSError):
		pass
	try:
		os.kill(pid, sig)
		delivered = True
	except (ProcessLookupError, OSError):
		pass
	return delivered

if not deliver(signal.SIGTERM):
	sys.exit(0)

deadline = time.monotonic() + 5
while time.monotonic() < deadline:
	if not alive():
		sys.exit(0)
	time.sleep(0.1)

deliver(signal.SIGKILL)
deadline = time.monotonic() + 2
while time.monotonic() < deadline:
	if not alive():
		sys.exit(0)
	time.sleep(0.1)

sys.exit(1 if alive() else 0)
PY
}

qfw_lrq_result_ok() {
	local result_file="$1"
	if [[ ! -s "${result_file}" ]]; then
		echo "ERROR: missing app result file: ${result_file}" >&2
		return 1
	fi
	python3 - "${result_file}" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as handle:
	records = [json.loads(line) for line in handle if line.strip()]
if any(record.get("kind") == "example" and record.get("status") == "ok"
	   for record in records):
	sys.exit(0)
print(f"ERROR: no successful example result in {path}", file=sys.stderr)
sys.exit(1)
PY
}

qfw_lrq_stop_dvm() {
	if [[ -s "${dvm_uri}" ]]; then
		echo "Stopping PRTE DVM ${dvm_uri}"
		qfw_lrq_on_node "${service_node}" pterm --dvm "file:${dvm_uri}" || true
	fi
	if qfw_lrq_bool_enabled "${QFW_LONG_RUNNING_QPM_FORCE_PRTE_CLEANUP:-no}"; then
		qfw_lrq_on_node "${service_node}" pkill -TERM prted || true
		qfw_lrq_on_node "${service_node}" pkill -TERM prte || true
	fi
}

qfw_lrq_cleanup() {
	set +e
	if [[ "${cleanup_started:-0}" == "1" ]]; then
		return 0
	fi
	cleanup_started=1
	qfw_lrq_kill_pid_file "${service_node:-localhost}" "${qpm_pid_file:-}"
	qfw_lrq_stop_dvm
	qfw_lrq_kill_pid_file "${service_node:-localhost}" "${dirsvc_pid_file:-}"
	set -e
}

qfw_lrq_exit() {
	local rc="$1"
	trap - EXIT
	qfw_lrq_cleanup
	qfw_example_finish "${rc}"
	exit "${rc}"
}

qfw_lrq_run_app() {
	local wave="$1"
	local app_index="$2"
	local app_node="$3"
	local app_dir="${run_dir}/waves/wave-${wave}/app-${app_index}"
	local app_run_dir="${app_dir}/runtime"
	local app_log="${app_dir}/app.log"
	local app_result="${app_dir}/result.jsonl"
	local setup_rc app_rc teardown_rc

	mkdir -p "${app_dir}"
	{
		echo "wave=${wave} app=${app_index} node=${app_node}"
		set +e
		qfw-setup \
			--site-config "${site_config}" \
			--runtime-config "${runtime_config}" \
			--run-dir "${app_run_dir}"
		setup_rc=$?
		set -e
		if [[ "${setup_rc}" -ne 0 ]]; then
			echo "qfw-setup failed with ${setup_rc}"
			return "${setup_rc}"
		fi
		if grep -q "QFW_LOCAL_DIRSVC_ENDPOINT" \
				"${app_run_dir}/state/runtime-state.json"; then
			echo "ERROR: site app runtime unexpectedly contains local dirsvc"
			qfw-teardown --run-dir "${app_run_dir}" || true
			return 1
		fi

		set +e
		QFW_EXAMPLE_RESULT_FILE="${app_result}" \
			QFW_SUPERMARQ_SHUTDOWN_QPM=no \
			qfw-srun \
				--run-dir "${app_run_dir}" \
				--nodes 1 \
				--ntasks 1 \
				--nodelist "${app_node}" \
				"$(qfw_example_path tests/test_supermarq.py)" \
				--run "${run_mode}" \
				--iterations "${supermarq_iterations}" \
				--startqbit "${startqbit}" \
				--shots "${shots}" \
				--increase "${increase}" \
				--method "${method}" \
				--backend "${backend}"
		app_rc=$?
		qfw-teardown --run-dir "${app_run_dir}"
		teardown_rc=$?
		set -e

		echo "app_rc=${app_rc} teardown_rc=${teardown_rc}"
		if [[ "${app_rc}" -ne 0 ]]; then
			return "${app_rc}"
		fi
		if [[ "${teardown_rc}" -ne 0 ]]; then
			return "${teardown_rc}"
		fi
		qfw_lrq_result_ok "${app_result}"
	} >"${app_log}" 2>&1
}

qfw_lrq_run_wave() {
	local wave="$1"
	local pids=()
	local rc=0
	local app_index app_node pid node_index

	echo "Starting wave ${wave} with ${apps} concurrent applications"
	for app_index in $(seq 1 "${apps}"); do
		node_index=$(( (app_index - 1) % ${#app_nodes[@]} ))
		app_node="${app_nodes[${node_index}]}"
		qfw_lrq_run_app "${wave}" "${app_index}" "${app_node}" &
		pids+=("$!")
	done

	for pid in "${pids[@]}"; do
		if ! wait "${pid}"; then
			rc=1
		fi
	done
	if [[ "${rc}" -ne 0 ]]; then
		return "${rc}"
	fi
	if ! qfw_lrq_pid_alive "${service_node}" "${qpm_pid_file}"; then
		echo "ERROR: long-running QPM exited during wave ${wave}" >&2
		return 1
	fi
	echo "Wave ${wave} completed"
}

qfw_lrq_require_positive_int "--apps" "${apps}"
qfw_lrq_require_positive_int "--waves" "${waves}"
qfw_lrq_require_positive_int "--timeout" "${startup_timeout}"
qfw_lrq_require_runtime

if [[ "${backend}" != "nwqsim" ]]; then
	echo "ERROR: this long-running example currently supports nwqsim only" >&2
	exit 2
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
if [[ -z "${run_dir}" ]]; then
	run_dir="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}/long-running-qpm-${timestamp}"
fi
mkdir -p "${run_dir}"

if qfw_lrq_bool_enabled "${QFW_LONG_RUNNING_QPM_TRACE:-no}"; then
	set -x
fi

qfw_example_begin "long-running-qpm" \
	"--apps=${apps}" "--waves=${waves}" "--backend=${backend}"
trap 'qfw_lrq_exit "$?"' EXIT

mapfile -t allocation_nodes < <(qfw_lrq_collect_allocation_nodes)
if [[ "${#allocation_nodes[@]}" -eq 0 ]]; then
	echo "ERROR: no allocation nodes found" >&2
	exit 1
fi

service_node="${service_node_override:-${allocation_nodes[0]}}"
if [[ -n "${app_nodes_override}" ]]; then
	mapfile -t app_nodes < <(qfw_lrq_expand_nodelist "${app_nodes_override}")
else
	app_nodes=()
	for node in "${allocation_nodes[@]}"; do
		if [[ "${node}" != "${service_node}" ]]; then
			app_nodes+=("${node}")
		fi
	done
fi

if [[ "${#app_nodes[@]}" -eq 0 ]]; then
	if qfw_lrq_bool_enabled "${allow_node_reuse}"; then
		app_nodes=("${service_node}")
	else
		echo "ERROR: no application nodes available" >&2
		exit 1
	fi
fi

if [[ "${apps}" -gt "${#app_nodes[@]}" ]] &&
   ! qfw_lrq_bool_enabled "${allow_node_reuse}"; then
	echo "ERROR: --apps=${apps} needs at least ${apps} app nodes; got ${#app_nodes[@]}" >&2
	exit 1
fi

dirsvc_port="${QFW_LONG_RUNNING_QPM_DIRSVC_PORT:-8090}"
qpm_port="${QFW_LONG_RUNNING_QPM_QPM_PORT:-8290}"
qpm_telnet_port="${QFW_LONG_RUNNING_QPM_QPM_TELNET_PORT:-8291}"
dvm_uri="${run_dir}/prte_dvm/dvm-uri"
dirsvc_pid_file="${run_dir}/state/dirsvc.pid"
dirsvc_ready_file="${run_dir}/state/dirsvc-ready.json"
qpm_pid_file="${run_dir}/state/${backend}.pid"
qpm_ready_file="${run_dir}/state/${backend}-ready.json"
summary_file="${run_dir}/summary.jsonl"
cleanup_started=0

mkdir -p "${run_dir}/state" "${run_dir}/logs"
: >"${summary_file}"

service_runtime_config="$(qfw_lrq_service_runtime_config)"
qfw_lrq_write_configs

echo "Run directory: ${run_dir}"
echo "Service node: ${service_node}"
echo "Application nodes: ${app_nodes[*]}"
echo "Site directory: ${service_node}:${dirsvc_port}"
echo "Service runtime config: ${service_runtime_config}"

qfw_lrq_start_dirsvc
qfw_lrq_start_dvm
qfw_lrq_start_qpm

for wave in $(seq 1 "${waves}"); do
	qfw_lrq_run_wave "${wave}"
done

find "${run_dir}/waves" -name result.jsonl -type f -exec cat {} \; \
	>>"${summary_file}"

echo "Long-running QPM example passed"
echo "Logs: ${run_dir}"
echo "Summary JSONL: ${summary_file}"
