#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

usage() {
	cat <<EOF
Usage: ./qfw_iqm_site_services.sh <start|stop|status|preflight|install-deps> [options]

Start, probe, or stop a Docker-hosted site-style DEFw directory service and
long-running ORNL IQM QPM service.

Options:
  --target-device DEVICE        QFw device id (default: QFW_QPU_DEVICE_ID or ornl-iqm-20q)
  --service-id ID               QPM service id (default: iqm-ornl-20q)
  --site-config PATH            Site config to use or generate
  --run-dir DIR                 Run directory for configs, pids, and logs
  --service-node NODE           Node that hosts dirsvc and QPM
  --dirsvc-port PORT            Site directory listen port (default: 8090)
  --qpm-port PORT               IQM QPM listen port (default: 8290)
  --qpm-telnet-port PORT        IQM QPM telnet port (default: 8291)
  --timeout SEC                 Startup and preflight timeout (default: 120)
  --venv DIR                    Virtual environment for install-deps
  --requirements PATH           Override IQM service requirements file
  --constraints PATH            Override IQM service constraints file
  --skip-preflight              Start services without telemetry preflight
  --dry-run                     Create ready files without launching processes
  -h, --help                    Show this help

The start action leaves the directory service and QPM running. Use the stop
action with the same --run-dir to terminate them.

The install-deps action installs the IQM QPM service dependencies into the
given virtual environment, or into the active virtual environment when --venv
is omitted.
EOF
}

action=""
target_device="${QFW_QPU_DEVICE_ID:-ornl-iqm-20q}"
service_id="${QFW_IQM_SERVICE_ID:-iqm-ornl-20q}"
site_config="${QFW_IQM_SITE_CONFIG:-}"
run_dir="${QFW_IQM_RUN_DIR:-}"
service_node="${QFW_IQM_SERVICE_NODE:-}"
dirsvc_port="${QFW_IQM_DIRSVC_PORT:-8090}"
qpm_port="${QFW_IQM_QPM_PORT:-8290}"
qpm_telnet_port="${QFW_IQM_QPM_TELNET_PORT:-8291}"
startup_timeout="${QFW_IQM_TIMEOUT:-120}"
deps_venv="${QFW_IQM_DEPS_VENV:-${QFW_SHARED_VENV:-}}"
requirements_path="${QFW_IQM_REQUIREMENTS:-}"
constraints_path="${QFW_IQM_CONSTRAINTS:-}"
run_preflight="${QFW_IQM_PREFLIGHT:-yes}"
dry_run="no"

qfw_iqm_need_value() {
	local option="${1:-option}"
	if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == --* ]]; then
		echo "ERROR: ${option} requires a value" >&2
		exit 2
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		start|stop|status|preflight|install-deps)
			if [[ -n "${action}" ]]; then
				echo "ERROR: multiple actions specified" >&2
				exit 2
			fi
			action="$1"
			shift
			;;
		--target-device)
			qfw_iqm_need_value "$@"
			target_device="$2"
			shift 2
			;;
		--service-id)
			qfw_iqm_need_value "$@"
			service_id="$2"
			shift 2
			;;
		--site-config)
			qfw_iqm_need_value "$@"
			site_config="$2"
			shift 2
			;;
		--run-dir)
			qfw_iqm_need_value "$@"
			run_dir="$2"
			shift 2
			;;
		--service-node)
			qfw_iqm_need_value "$@"
			service_node="$2"
			shift 2
			;;
		--dirsvc-port)
			qfw_iqm_need_value "$@"
			dirsvc_port="$2"
			shift 2
			;;
		--qpm-port)
			qfw_iqm_need_value "$@"
			qpm_port="$2"
			shift 2
			;;
		--qpm-telnet-port)
			qfw_iqm_need_value "$@"
			qpm_telnet_port="$2"
			shift 2
			;;
		--timeout)
			qfw_iqm_need_value "$@"
			startup_timeout="$2"
			shift 2
			;;
		--venv)
			qfw_iqm_need_value "$@"
			deps_venv="$2"
			shift 2
			;;
		--requirements)
			qfw_iqm_need_value "$@"
			requirements_path="$2"
			shift 2
			;;
		--constraints)
			qfw_iqm_need_value "$@"
			constraints_path="$2"
			shift 2
			;;
		--skip-preflight)
			run_preflight="no"
			shift
			;;
		--dry-run)
			dry_run="yes"
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

if [[ -z "${action}" ]]; then
	usage >&2
	exit 2
fi

qfw_iqm_bool_enabled() {
	case "${1:-}" in
		1|yes|true|on|y|YES|TRUE|ON|Y) return 0 ;;
		*) return 1 ;;
	esac
}

qfw_iqm_require_positive_int() {
	local name="$1"
	local value="$2"
	if ! [[ "${value}" =~ ^[0-9]+$ ]] || [[ "${value}" -le 0 ]]; then
		echo "ERROR: ${name} must be a positive integer: ${value}" >&2
		exit 2
	fi
}

qfw_iqm_expand_nodelist() {
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

qfw_iqm_collect_allocation_nodes() {
	local raw_lists=()
	if [[ -n "${QFW_IQM_NODELIST:-}" ]]; then
		raw_lists+=("${QFW_IQM_NODELIST}")
	elif [[ -n "${SLURM_JOB_NODELIST:-}" ]]; then
		raw_lists+=("${SLURM_JOB_NODELIST}")
	else
		hostname
		return 0
	fi

	local raw
	for raw in "${raw_lists[@]}"; do
		qfw_iqm_expand_nodelist "${raw}"
	done | awk 'NF && !seen[$0]++'
}

qfw_iqm_on_node() {
	local node="$1"
	shift
	if [[ -n "${SLURM_JOB_ID:-}" &&
	      "${QFW_IQM_DISABLE_SRUN:-no}" != "yes" ]]; then
		srun --nodes=1 --ntasks=1 --nodelist "${node}" "$@"
	else
		"$@"
	fi
}

qfw_iqm_require_runtime() {
	qfw_example_require_runtime
	local command_name
	for command_name in qfw-dirsvc-start qfw-service-start; do
		if ! command -v "${command_name}" >/dev/null 2>&1; then
			echo "ERROR: ${command_name} is not in PATH" >&2
			return 1
		fi
	done
}

qfw_iqm_source_root() {
	if [[ -n "${QFW_SRC:-}" && -d "${QFW_SRC}" ]]; then
		printf "%s\n" "${QFW_SRC}"
		return 0
	fi
	if [[ -r "${script_dir}/../CMakeLists.txt" ]]; then
		(cd "${script_dir}/.." && pwd)
		return 0
	fi
	return 1
}

qfw_iqm_service_deps_dir() {
	local source_root candidate
	source_root="$(qfw_iqm_source_root 2>/dev/null || true)"
	for candidate in \
		"${QFW_PREFIX:-}/lib/qfw/services/svc_iqm_qpm" \
		"${QFW_PREFIX:-}/lib64/qfw/services/svc_iqm_qpm" \
		"${source_root}/services/svc_iqm_qpm"; do
		if [[ -n "${candidate}" && -d "${candidate}" ]]; then
			printf "%s\n" "${candidate}"
			return 0
		fi
	done
	echo "ERROR: unable to locate svc_iqm_qpm dependency files" >&2
	return 1
}

qfw_iqm_default_requirements_path() {
	if [[ -n "${requirements_path}" ]]; then
		printf "%s\n" "${requirements_path}"
		return 0
	fi
	local deps_dir
	deps_dir="$(qfw_iqm_service_deps_dir)"
	printf "%s\n" "${deps_dir}/requirements.in"
}

qfw_iqm_default_constraints_path() {
	if [[ -n "${constraints_path}" ]]; then
		printf "%s\n" "${constraints_path}"
		return 0
	fi
	local deps_dir
	deps_dir="$(qfw_iqm_service_deps_dir)"
	printf "%s\n" "${deps_dir}/constraints.txt"
}

qfw_iqm_install_deps_emit() {
	local status="$1"
	local rc="$2"
	local python_bin="$3"
	local requirements_file="$4"
	local constraints_file="$5"
	python3 - "${status}" "${rc}" "${python_bin}" "${requirements_file}" \
		"${constraints_file}" "${deps_venv}" "${dry_run}" <<'PY'
import json
import sys
import time

(
	status,
	rc,
	python_bin,
	requirements_file,
	constraints_file,
	deps_venv,
	dry_run,
) = sys.argv[1:]
record = {
	"schema": "qfw-iqm-install-deps-v1",
	"kind": "iqm-install-deps",
	"status": status,
	"rc": int(rc),
	"python": python_bin,
	"venv": deps_venv,
	"requirements": requirements_file,
	"constraints": constraints_file,
	"dry_run": dry_run == "yes",
	"timestamp_ns": time.time_ns(),
}
print("QFW_IQM_INSTALL_DEPS_RESULT " + json.dumps(record, sort_keys=True))
PY
}

qfw_iqm_install_deps() {
	local requirements_file constraints_file python_bin
	requirements_file="$(qfw_iqm_default_requirements_path)"
	constraints_file="$(qfw_iqm_default_constraints_path)"
	if [[ ! -r "${requirements_file}" ]]; then
		echo "ERROR: IQM requirements file not readable: ${requirements_file}" >&2
		return 1
	fi
	if [[ -n "${constraints_file}" && ! -r "${constraints_file}" ]]; then
		echo "ERROR: IQM constraints file not readable: ${constraints_file}" >&2
		return 1
	fi

	if [[ -n "${deps_venv}" ]]; then
		python_bin="${deps_venv%/}/bin/python"
	elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
		deps_venv="${VIRTUAL_ENV}"
		python_bin="${VIRTUAL_ENV%/}/bin/python"
	else
		python_bin="$(command -v python3 || true)"
	fi
	if qfw_iqm_bool_enabled "${dry_run}"; then
		python_bin="${python_bin:-python3}"
	elif [[ -z "${python_bin}" || ! -x "${python_bin}" ]]; then
		echo "ERROR: Python executable not found for install-deps" >&2
		return 1
	fi

	local command=(
		"${python_bin}"
		-m pip install
		-r "${requirements_file}"
	)
	if [[ -n "${constraints_file}" ]]; then
		command+=(-c "${constraints_file}")
	fi

	printf "Installing IQM QPM dependencies with: "
	printf "%q " "${command[@]}"
	printf "\n"
	if qfw_iqm_bool_enabled "${dry_run}"; then
		qfw_iqm_install_deps_emit "dry-run" 0 "${python_bin}" \
			"${requirements_file}" "${constraints_file}"
		return 0
	fi

	local rc
	set +e
	"${command[@]}"
	rc=$?
	set -e
	local status="ok"
	if [[ "${rc}" -ne 0 ]]; then
		status="error"
	fi
	qfw_iqm_install_deps_emit "${status}" "${rc}" "${python_bin}" \
		"${requirements_file}" "${constraints_file}"
	return "${rc}"
}

qfw_iqm_prepare_defaults() {
	qfw_iqm_require_positive_int "--dirsvc-port" "${dirsvc_port}"
	qfw_iqm_require_positive_int "--qpm-port" "${qpm_port}"
	qfw_iqm_require_positive_int "--qpm-telnet-port" "${qpm_telnet_port}"
	qfw_iqm_require_positive_int "--timeout" "${startup_timeout}"

	if [[ -z "${run_dir}" ]]; then
		local timestamp
		timestamp="$(date +%Y%m%d-%H%M%S)"
		run_dir="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}/iqm-site-${timestamp}"
	fi
	mkdir -p "${run_dir}/config" "${run_dir}/state" "${run_dir}/logs" \
		"${run_dir}/env"

	if [[ -z "${service_node}" ]]; then
		mapfile -t qfw_iqm_nodes < <(qfw_iqm_collect_allocation_nodes)
		if [[ "${#qfw_iqm_nodes[@]}" -eq 0 ]]; then
			echo "ERROR: no service node found" >&2
			exit 1
		fi
		service_node="${qfw_iqm_nodes[0]}"
	fi

	if [[ -z "${site_config}" ]]; then
		site_config="${run_dir}/config/iqm-site.yaml"
	fi
	service_manifest="${QFW_SHARE_DIR%/}/config/services/site-services.yaml"
	device_access_config="${QFW_PREFIX%/}/lib/qfw/services/dev-config/config.yaml"
	dirsvc_pid_file="${run_dir}/state/dirsvc.pid"
	dirsvc_ready_file="${run_dir}/state/dirsvc-ready.json"
	qpm_pid_file="${run_dir}/state/${service_id}.pid"
	qpm_ready_file="${run_dir}/state/${service_id}-ready.json"
	site_runtime_config="${run_dir}/config/iqm-site-runtime.yaml"
	site_env_file="${run_dir}/env/iqm-site.env"
}

qfw_iqm_write_site_config() {
	if [[ -r "${site_config}" ]]; then
		return 0
	fi
	local qfw_prefix defw_prefix
	qfw_prefix="${QFW_PREFIX:-}"
	defw_prefix="${DEFW_PREFIX:-${qfw_prefix}}"
	if [[ -z "${qfw_prefix}" ]]; then
		echo "ERROR: QFW_PREFIX is not set; source qfw-activate first" >&2
		return 1
	fi
	cat >"${site_config}" <<EOF
install:
  qfw-prefix: ${qfw_prefix}
  defw-prefix: ${defw_prefix}

directory:
  site:
    name: qfw-docker-iqm-dirsvc
    endpoint: ${service_node}:${dirsvc_port}
    connect-timeout-seconds: ${startup_timeout}

service:
  manifest: ${service_manifest}
  device-access-config: ${device_access_config}

qpm:
  completion-queues:
    retention:
      completion-ttl-seconds: 3600
      terminal-reservation-retention-seconds: 3600
      max-records-per-reservation: 1024
      max-bytes-per-reservation: 67108864
      purge-interval-seconds: 60
EOF
}

qfw_iqm_write_site_runtime_config() {
	cat >"${site_runtime_config}" <<EOF
resolver:
  scope-order:
    - site
EOF
}

qfw_iqm_write_env_file() {
	cat >"${site_env_file}" <<EOF
export QFW_IQM_RUN_DIR=${run_dir}
export QFW_SITE_CONFIG=${site_config}
export QFW_RUNTIME_CONFIG=${site_runtime_config}
export QFW_QPU_DEVICE_ID=${target_device}
export QFW_IQM_SERVICE_ID=${service_id}
export QFW_SITE_DIRSVC_ENDPOINTS=${service_node}:${dirsvc_port}
EOF
}

qfw_iqm_pid_alive() {
	local node="$1"
	local pid_file="$2"
	if [[ ! -r "${pid_file}" ]]; then
		return 1
	fi
	local pid
	pid="$(<"${pid_file}")"
	qfw_iqm_on_node "${node}" python3 - "${pid}" <<'PY'
import os
import sys

try:
	os.kill(int(sys.argv[1]), 0)
except OSError:
	sys.exit(1)
PY
}

qfw_iqm_kill_pid_file() {
	local node="$1"
	local pid_file="$2"
	if [[ ! -r "${pid_file}" ]]; then
		return 0
	fi
	local pid
	pid="$(<"${pid_file}")"
	echo "Stopping pid ${pid} on ${node}"
	qfw_iqm_on_node "${node}" python3 - "${pid}" <<'PY'
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

qfw_iqm_start_dirsvc() {
	echo "Starting IQM site dirsvc on ${service_node}:${dirsvc_port}"
	local command=(
		qfw-dirsvc-start \
			--background \
			--site-config "${site_config}" \
			--run-dir "${run_dir}" \
			--name qfw-docker-iqm-dirsvc \
			--host "${service_node}" \
			--listen-port "${dirsvc_port}" \
			--timeout "${startup_timeout}" \
			--pid-file "${dirsvc_pid_file}" \
			--ready-file "${dirsvc_ready_file}"
	)
	if qfw_iqm_bool_enabled "${dry_run}"; then
		command+=(--dry-run)
	fi
	qfw_iqm_on_node "${service_node}" "${command[@]}"
}

qfw_iqm_start_qpm() {
	echo "Starting long-running IQM QPM ${service_id} for ${target_device}"
	(
		export QFW_SERVICE_SCOPE="site"
		export QFW_QPU_DEVICE_ID="${target_device}"
		export QFW_SITE_DIRSVC_ENDPOINTS="${service_node}:${dirsvc_port}"
		export QFW_SITE_DIRSVC_NAME="qfw-docker-iqm-dirsvc"
		local command=(
			qfw-service-start \
				--background \
				--run-dir "${run_dir}" \
				--service-id "${service_id}" \
				--site-config "${site_config}" \
				--operation-mode long-running \
				--listen-port "${qpm_port}" \
				--telnet-port "${qpm_telnet_port}" \
				--timeout "${startup_timeout}" \
				--pid-file "${qpm_pid_file}" \
				--ready-file "${qpm_ready_file}"
		)
		if qfw_iqm_bool_enabled "${dry_run}"; then
			command+=(--dry-run)
		fi
		qfw_iqm_on_node "${service_node}" "${command[@]}"
	)
}

qfw_iqm_write_preflight_script() {
	preflight_script="${run_dir}/config/iqm-preflight.py"
	cat >"${preflight_script}" <<'PY'
import argparse
import json
import os
import sys
import time
import traceback

import defw
from defw_app_util import defw_get_directory_service
from qfw_qiskit.qpm_resolver import QPMResolver
from qfw_qiskit.qpm_selection import qpm_selection_for_provider


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--target-device", required=True)
	parser.add_argument("--timeout", type=int, default=120)
	args = parser.parse_args()

	start = time.time_ns()
	try:
		try:
			dirsvc = defw_get_directory_service()
		except Exception:
			dirsvc = None
		resolver = QPMResolver.from_environment(
			dirsvc=dirsvc,
			defw_module=defw,
		)
		request = qpm_selection_for_provider("iqm")
		request.update({
			"timeout": args.timeout,
			"api_category": "telemetry",
			"selector_resource": args.target_device,
		})
		qpm = resolver.connect(**request)
		device_info = qpm.get_device_info()
		record = {
			"schema": "qfw-iqm-preflight-v1",
			"kind": "iqm-preflight",
			"status": "ok",
			"target_device": args.target_device,
			"device_info_type": type(device_info).__name__,
			"device_info": device_info,
			"timestamp_ns": time.time_ns(),
			"duration_sec": (time.time_ns() - start) / 1e9,
		}
		print("QFW_IQM_PREFLIGHT_RESULT " + json.dumps(record, sort_keys=True))
		return 0
	except Exception as exc:
		record = {
			"schema": "qfw-iqm-preflight-v1",
			"kind": "iqm-preflight",
			"status": "error",
			"target_device": args.target_device,
			"error": str(exc),
			"error_type": type(exc).__name__,
			"traceback": traceback.format_exc(),
			"timestamp_ns": time.time_ns(),
			"duration_sec": (time.time_ns() - start) / 1e9,
		}
		print("QFW_IQM_PREFLIGHT_RESULT " + json.dumps(record, sort_keys=True))
		return 1


if __name__ == "__main__":
	sys.exit(main())
PY
}

qfw_iqm_preflight() {
	qfw_iqm_write_preflight_script
	local preflight_dir="${run_dir}/preflight"
	local preflight_run_dir="${preflight_dir}/runtime"
	local stdout_log="${run_dir}/logs/iqm-preflight.stdout.log"
	local stderr_log="${run_dir}/logs/iqm-preflight.stderr.log"
	mkdir -p "${preflight_dir}"
	echo "Running IQM telemetry preflight for ${target_device}"
	set +e
	QFW_SITE_CONFIG="${site_config}" \
	QFW_RUNTIME_CONFIG="${site_runtime_config}" \
	QFW_QPU_DEVICE_ID="${target_device}" \
		qfw-setup \
			--site-config "${site_config}" \
			--runtime-config "${site_runtime_config}" \
			--run-dir "${preflight_run_dir}" \
		>"${stdout_log}" 2>"${stderr_log}"
	local setup_rc=$?
	if [[ "${setup_rc}" -eq 0 ]]; then
		QFW_QPU_DEVICE_ID="${target_device}" \
			qfw-srun \
				--run-dir "${preflight_run_dir}" \
				--nodes 1 \
				--ntasks 1 \
				"${preflight_script}" \
				--target-device "${target_device}" \
				--timeout "${startup_timeout}" \
			>>"${stdout_log}" 2>>"${stderr_log}"
		local run_rc=$?
		qfw-teardown --run-dir "${preflight_run_dir}" \
			>>"${stdout_log}" 2>>"${stderr_log}"
		local teardown_rc=$?
		set -e
		if [[ "${run_rc}" -ne 0 ]]; then
			return "${run_rc}"
		fi
		return "${teardown_rc}"
	fi
	set -e
	return "${setup_rc}"
}

qfw_iqm_emit_status() {
	local dirsvc_alive="false"
	local qpm_alive="false"
	if qfw_iqm_pid_alive "${service_node}" "${dirsvc_pid_file}"; then
		dirsvc_alive="true"
	fi
	if qfw_iqm_pid_alive "${service_node}" "${qpm_pid_file}"; then
		qpm_alive="true"
	fi
	python3 - "${run_dir}" "${service_node}" "${target_device}" \
		"${service_id}" "${site_config}" "${service_manifest}" \
		"${device_access_config}" "${dirsvc_alive}" "${qpm_alive}" \
		"${dry_run}" <<'PY'
import json
import sys
import time

(
	run_dir,
	service_node,
	target_device,
	service_id,
	site_config,
	service_manifest,
	device_access_config,
	dirsvc_alive,
	qpm_alive,
	dry_run,
) = sys.argv[1:]
status = "dry-run"
if dry_run != "yes":
	status = (
		"ok"
		if dirsvc_alive == "true" and qpm_alive == "true" else
		"not-ready"
	)
record = {
	"schema": "qfw-iqm-site-services-v1",
	"kind": "iqm-site-services",
	"status": status,
	"run_dir": run_dir,
	"service_node": service_node,
	"target_device": target_device,
	"service_id": service_id,
	"site_config": site_config,
	"service_manifest": service_manifest,
	"device_access_config": device_access_config,
	"dirsvc_alive": dirsvc_alive == "true",
	"qpm_alive": qpm_alive == "true",
	"dry_run": dry_run == "yes",
	"dirsvc_log_stdout": f"{run_dir}/logs/qfw-docker-iqm-dirsvc.stdout.log",
	"dirsvc_log_stderr": f"{run_dir}/logs/qfw-docker-iqm-dirsvc.stderr.log",
	"qpm_log_stdout": f"{run_dir}/logs/{service_id}.stdout.log",
	"qpm_log_stderr": f"{run_dir}/logs/{service_id}.stderr.log",
	"timestamp_ns": time.time_ns(),
}
print("QFW_IQM_SITE_SERVICES_RESULT " + json.dumps(record, sort_keys=True))
PY
}

if [[ "${action}" == "install-deps" ]]; then
	qfw_iqm_install_deps
	exit "$?"
fi

qfw_iqm_prepare_defaults

case "${action}" in
	start|preflight)
		qfw_iqm_require_runtime
		qfw_iqm_write_site_config
		qfw_iqm_write_site_runtime_config
		qfw_iqm_write_env_file
		;;
	stop|status)
		if [[ -r "${site_env_file}" ]]; then
			# shellcheck source=/dev/null
			source "${site_env_file}"
		fi
		;;
esac

case "${action}" in
	start)
		qfw_iqm_start_dirsvc
		qfw_iqm_start_qpm
		if qfw_iqm_bool_enabled "${run_preflight}"; then
			qfw_iqm_preflight
		fi
		qfw_iqm_emit_status
		if qfw_iqm_bool_enabled "${dry_run}"; then
			echo "IQM site services dry-run complete"
		else
			echo "IQM site services are running"
		fi
		echo "Run directory: ${run_dir}"
		echo "Environment: ${site_env_file}"
		;;
	preflight)
		qfw_iqm_preflight
		;;
	status)
		qfw_iqm_emit_status
		;;
	stop)
		qfw_iqm_kill_pid_file "${service_node}" "${qpm_pid_file}"
		qfw_iqm_kill_pid_file "${service_node}" "${dirsvc_pid_file}"
		qfw_iqm_emit_status
		;;
esac
