#!/bin/bash

_qfw_example_common_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "${QFW_EXAMPLES_DIR:-}" ]]; then
	if [[ -n "${QFW_SHARE_DIR:-}" && -d "${QFW_SHARE_DIR}/examples" ]]; then
		export QFW_EXAMPLES_DIR="${QFW_SHARE_DIR}/examples"
	else
		export QFW_EXAMPLES_DIR="${_qfw_example_common_dir}"
	fi
fi

qfw_example_examples_dir() {
	printf "%s\n" "${QFW_EXAMPLES_DIR}"
}

qfw_example_path() {
	local relative="$1"
	case "${relative}" in
		/*) printf "%s\n" "${relative}" ;;
		*)  printf "%s/%s\n" "${QFW_EXAMPLES_DIR%/}" "${relative#./}" ;;
	esac
}

qfw_example_parse_common_options() {
	local verbose="${QFW_EXAMPLE_VERBOSE:-no}"

	while [[ "${1:-}" == "--verbose" ]]; do
		verbose="yes"
		shift
	done
	QFW_EXAMPLE_REMAINING_ARGS=("$@")
	export QFW_EXAMPLE_VERBOSE="${verbose}"

	case "${verbose}" in
		1|yes|true|on|y|YES|TRUE|ON|Y) set -x ;;
	esac
}

qfw_example_parse_execution_options() {
	local verbose="${QFW_EXAMPLE_VERBOSE:-no}"
	local service_mode="${QFW_EXAMPLE_SERVICE_MODE:-local}"
	local backend="${QFW_EXAMPLE_BACKEND:-}"
	local site_config="${QFW_EXAMPLE_SITE_CONFIG_PATH:-${QFW_SITE_CONFIG:-}}"
	local runtime_config="${QFW_EXAMPLE_RUNTIME_CONFIG_PATH:-${QFW_RUNTIME_CONFIG:-}}"

	while [[ $# -gt 0 ]]; do
		case "$1" in
			--verbose)
				verbose="yes"
				shift
				;;
			--service-mode)
				if [[ $# -lt 2 || -z "${2:-}" ]]; then
					echo "ERROR: --service-mode requires local or site" >&2
					return 2
				fi
				service_mode="$2"
				shift 2
				;;
			--backend)
				if [[ $# -lt 2 || -z "${2:-}" ]]; then
					echo "ERROR: --backend requires a value" >&2
					return 2
				fi
				backend="$2"
				shift 2
				;;
			--site-config)
				if [[ $# -lt 2 || -z "${2:-}" ]]; then
					echo "ERROR: --site-config requires a path" >&2
					return 2
				fi
				site_config="$2"
				shift 2
				;;
			--runtime-config)
				if [[ $# -lt 2 || -z "${2:-}" ]]; then
					echo "ERROR: --runtime-config requires a path" >&2
					return 2
				fi
				runtime_config="$2"
				shift 2
				;;
			--)
				shift
				break
				;;
			*)
				break
				;;
		esac
	done

	case "${service_mode}" in
		local|site) ;;
		*)
			echo "ERROR: --service-mode must be local or site: ${service_mode}" >&2
			return 2
			;;
	esac

	QFW_EXAMPLE_REMAINING_ARGS=("$@")
	export QFW_EXAMPLE_VERBOSE="${verbose}"
	export QFW_EXAMPLE_SERVICE_MODE="${service_mode}"
	export QFW_EXAMPLE_BACKEND="${backend}"
	export QFW_EXAMPLE_SITE_CONFIG_PATH="${site_config}"
	export QFW_EXAMPLE_RUNTIME_CONFIG_PATH="${runtime_config}"

	case "${verbose}" in
		1|yes|true|on|y|YES|TRUE|ON|Y) set -x ;;
	esac
}

qfw_example_begin() {
	QFW_EXAMPLE_NAME="$1"
	shift || true
	QFW_EXAMPLE_ARGS=("$@")
	QFW_EXAMPLE_START_EPOCH="$(date +%s)"
	QFW_EXAMPLE_SETUP_STARTED=0
	QFW_EXAMPLE_TEARDOWN_DONE=0
	QFW_EXAMPLE_RUNTIME_CONFIG=""
	QFW_EXAMPLE_SITE_CONFIG=""
	export QFW_EXAMPLE_NAME
	trap 'qfw_example_exit "$?"' EXIT
	qfw_example_emit "start" "running" 0 0
}

qfw_example_require_runtime() {
	if [[ ! -d "${QFW_EXAMPLES_DIR}" ]]; then
		echo "ERROR: QFw examples directory not found: ${QFW_EXAMPLES_DIR}" >&2
		return 1
	fi
	local command_name
	for command_name in qfw-setup qfw-srun qfw-teardown; do
		if ! command -v "${command_name}" >/dev/null 2>&1; then
			echo "ERROR: ${command_name} is not in PATH. Source qfw-activate first." >&2
			return 1
		fi
	done
}

qfw_example_setup() {
	qfw_example_require_runtime
	QFW_EXAMPLE_SETUP_STARTED=1
	qfw-setup "$@"
}

qfw_example_make_local_runtime_config() {
	if [[ $# -lt 1 ]]; then
		echo "ERROR: qfw_example_make_local_runtime_config requires a manifest path" >&2
		return 2
	fi
	local manifest_path
	manifest_path="$(qfw_example_path "$1")"
	shift
	if [[ ! -r "${manifest_path}" ]]; then
		echo "ERROR: service manifest not readable: ${manifest_path}" >&2
		return 1
	fi

	local runtime_base runtime_dir runtime_config
	runtime_base="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}"
	runtime_dir="${runtime_base%/}/example-runtime"
	mkdir -p "${runtime_dir}"
	runtime_config="$(mktemp \
		"${runtime_dir}/${QFW_EXAMPLE_NAME:-qfw}-runtime.XXXXXX.yaml")"

	python3 - "${runtime_config}" "${manifest_path}" "$@" <<'PY'
import json
import sys

runtime_path, manifest_path, *services = sys.argv[1:]
with open(runtime_path, "w", encoding="utf-8") as stream:
	stream.write("resolver:\n")
	stream.write("  scope-order:\n")
	stream.write("    - local\n")
	stream.write("\n")
	stream.write("local-services:\n")
	stream.write("  start-dirsvc: true\n")
	stream.write("  start-qpm: true\n")
	stream.write("  dirsvc:\n")
	stream.write("    name: qfw-local-dirsvc\n")
	stream.write("    bind-host: 127.0.0.1\n")
	stream.write("    port: auto\n")
	stream.write(f"  service-manifest: {json.dumps(manifest_path)}\n")
	if services:
		stream.write("  services:\n")
		for service in services:
			stream.write(f"    - {json.dumps(service)}\n")
PY
	printf "%s\n" "${runtime_config}"
}

qfw_example_make_site_config() {
	if [[ $# -ne 1 || ! -r "$1" ]]; then
		echo "ERROR: qfw_example_make_site_config requires a readable device-access config" >&2
		return 2
	fi
	local runtime_base runtime_dir site_config
	runtime_base="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}"
	runtime_dir="${runtime_base%/}/example-runtime"
	mkdir -p "${runtime_dir}"
	site_config="$(mktemp \
		"${runtime_dir}/${QFW_EXAMPLE_NAME:-qfw}-site.XXXXXX.yaml")"
	python3 - "${QFW_SITE_CONFIG}" "$1" "${site_config}" <<'PY'
import sys

import yaml

source_path, device_path, output_path = sys.argv[1:]
with open(source_path, "r", encoding="utf-8") as stream:
    site = yaml.safe_load(stream) or {}
site.setdefault("service", {})["device-access-config"] = device_path
with open(output_path, "w", encoding="utf-8") as stream:
    yaml.safe_dump(site, stream, sort_keys=False)
PY
	printf "%s\n" "${site_config}"
}

qfw_example_setup_local_services() {
	if [[ $# -lt 1 ]]; then
		echo "ERROR: qfw_example_setup_local_services requires a manifest path" >&2
		return 2
	fi
	local runtime_config
	runtime_config="$(qfw_example_make_local_runtime_config "$@")"
	QFW_EXAMPLE_RUNTIME_CONFIG="${runtime_config}"
	qfw_example_setup --runtime-config "${runtime_config}"
}

qfw_example_setup_site_services() {
	local site_config="${QFW_EXAMPLE_SITE_CONFIG_PATH:-${QFW_SITE_CONFIG:-}}"
	local runtime_config="${QFW_EXAMPLE_RUNTIME_CONFIG_PATH:-${QFW_RUNTIME_CONFIG:-}}"
	if [[ -z "${site_config}" || ! -r "${site_config}" ]]; then
		echo "ERROR: site service mode requires a readable --site-config" >&2
		return 2
	fi
	if [[ -z "${runtime_config}" || ! -r "${runtime_config}" ]]; then
		echo "ERROR: site service mode requires a readable --runtime-config" >&2
		return 2
	fi
	qfw_example_setup \
		--site-config "${site_config}" \
		--runtime-config "${runtime_config}"
}

qfw_example_default_service_manifest() {
	if [[ -n "${QFW_EXAMPLE_SERVICE_MANIFEST:-}" ]]; then
		printf "%s\n" "${QFW_EXAMPLE_SERVICE_MANIFEST}"
	elif [[ -n "${QFW_SHARE_DIR:-}" &&
	        -r "${QFW_SHARE_DIR%/}/config/services/local-services.yaml" ]]; then
		printf "%s\n" "${QFW_SHARE_DIR%/}/config/services/local-services.yaml"
	else
		qfw_example_path "../share/qfw/config/services/local-services.yaml"
	fi
}

qfw_example_service_for_backend() {
	local backend="${1:-}"
	case "${backend}" in
		""|"tnqvm") printf "%s\n" "tnqvm" ;;
		"nwqsim")  printf "%s\n" "nwqsim" ;;
		"fake-iqm"|"fake-iqm-20q") printf "%s\n" "fake-iqm" ;;
		*)
			echo "ERROR: no local QPM service mapping for backend '${backend}'" >&2
			return 1
			;;
	esac
}

qfw_example_setup_qpm_services() {
	local manifest_path
	manifest_path="$(qfw_example_default_service_manifest)"
	qfw_example_setup_local_services "${manifest_path}" "$@"
}

qfw_example_setup_backend_service() {
	if [[ $# -lt 1 ]]; then
		echo "ERROR: qfw_example_setup_backend_service requires a backend" >&2
		return 2
	fi
	case "${QFW_EXAMPLE_SERVICE_MODE:-local}" in
		local)
			local service_name
			service_name="$(qfw_example_service_for_backend "$1")"
			qfw_example_setup_qpm_services "${service_name}"
			;;
		site)
			qfw_example_setup_site_services
			;;
		*)
			echo "ERROR: unsupported service mode: ${QFW_EXAMPLE_SERVICE_MODE}" >&2
			return 2
			;;
	esac
}

qfw_example_backend() {
	local fallback="${1:-nwqsim}"
	printf "%s\n" "${QFW_EXAMPLE_BACKEND:-${fallback}}"
}

qfw_example_result_is_terminal_success() {
	local result_file="${1:-}"
	if [[ -z "${result_file}" || ! -s "${result_file}" ]]; then
		return 1
	fi
	python3 - "${result_file}" <<'PY'
import json
import sys

terminal = None
try:
	with open(sys.argv[1], "r", encoding="utf-8") as stream:
		for line in stream:
			if not line.strip():
				continue
			record = json.loads(line)
			if record.get("kind") == "wrapper" and record.get("event") == "finish":
				terminal = record
except (OSError, ValueError):
	raise SystemExit(1)

if terminal is None:
	raise SystemExit(1)
if terminal.get("status") != "ok" or terminal.get("rc") != 0:
	raise SystemExit(1)
PY
}

qfw_example_srun() {
	qfw-srun "$@"
}

qfw_example_slurm_driver() {
	"$(qfw_example_path qfw_slurm_driver.sh)" "$@"
}

qfw_example_srun_with_backend_reservation() {
	if [[ $# -lt 7 ]]; then
		echo "ERROR: qfw_example_srun_with_backend_reservation requires backend, example, qubits, shots, count, operation, and application" >&2
		return 2
	fi
	local backend="$1"
	local example="$2"
	local qubits="$3"
	local shots="$4"
	local count="$5"
	local operation="$6"
	shift 6

	qfw_example_slurm_driver \
		--backend "${backend}" \
		--example "${example}" \
		--qubits "${qubits}" \
		--shots "${shots}" \
		--count "${count}" \
		--operation "${operation}" \
		--nodes 1 \
		--ntasks 1 \
		-- "$@"
}

qfw_example_srun_with_modules() {
	if [[ $# -lt 2 ]]; then
		echo "ERROR: qfw_example_srun_with_modules requires modules and application" >&2
		return 2
	fi
	local modules="$1"
	shift
	(
		export DEFW_ONLY_LOAD_MODULE="${modules}"
		qfw_example_srun "$@"
	)
}

qfw_example_teardown() {
	if [[ "${QFW_EXAMPLE_SETUP_STARTED:-0}" == "1" &&
	      "${QFW_EXAMPLE_TEARDOWN_DONE:-0}" == "0" ]]; then
		QFW_EXAMPLE_TEARDOWN_DONE=1
		qfw-teardown
	fi
}

qfw_example_exit() {
	local rc="$1"
	trap - EXIT
	qfw_example_finish "${rc}"
	exit "${rc}"
}

qfw_example_finish() {
	trap - EXIT
	local rc="$1"
	local teardown_rc=0
	if [[ "${QFW_EXAMPLE_SETUP_STARTED:-0}" == "1" &&
	      "${QFW_EXAMPLE_TEARDOWN_DONE:-0}" == "0" ]]; then
		QFW_EXAMPLE_TEARDOWN_DONE=1
		qfw-teardown || teardown_rc=$?
	fi
	if [[ -n "${QFW_EXAMPLE_RUNTIME_CONFIG:-}" &&
	      -f "${QFW_EXAMPLE_RUNTIME_CONFIG}" ]]; then
		rm -f "${QFW_EXAMPLE_RUNTIME_CONFIG}"
	fi
	if [[ -n "${QFW_EXAMPLE_SITE_CONFIG:-}" &&
	      -f "${QFW_EXAMPLE_SITE_CONFIG}" ]]; then
		rm -f "${QFW_EXAMPLE_SITE_CONFIG}"
	fi
	local now duration status
	now="$(date +%s)"
	duration=$((now - QFW_EXAMPLE_START_EPOCH))
	status="ok"
	if [[ "${rc}" -ne 0 || "${teardown_rc}" -ne 0 ]]; then
		status="error"
	fi
	qfw_example_emit "finish" "${status}" "${rc}" "${duration}" "${teardown_rc}"
}

qfw_example_emit() {
	local event="$1"
	local status="$2"
	local rc="$3"
	local duration="$4"
	local teardown_rc="${5:-0}"
	python3 - "$event" "$status" "$rc" "$duration" "$teardown_rc" \
		"${QFW_EXAMPLE_RESULT_FILE:-}" "${QFW_EXAMPLE_NAME:-unknown}" \
		"${QFW_EXAMPLE_ARGS[@]}" <<'PY'
import json
import os
import sys
import time

event, status, rc, duration, teardown_rc, path, name, *args = sys.argv[1:]
record = {
	"schema": "qfw-example-wrapper-v1",
	"kind": "wrapper",
	"event": event,
	"example": name,
	"status": status,
	"rc": int(rc),
	"teardown_rc": int(teardown_rc),
	"duration_sec": float(duration),
	"timestamp_ns": time.time_ns(),
	"args": args,
}
line = "QFW_EXAMPLE_RESULT " + json.dumps(
	record, indent=2, sort_keys=True)
print(line)
if path:
	directory = os.path.dirname(path)
	if directory:
		os.makedirs(directory, exist_ok=True)
	with open(path, "a", encoding="utf-8") as handle:
		handle.write(json.dumps(record, sort_keys=True) + "\n")
PY
}
