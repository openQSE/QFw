#!/bin/bash

set -euo pipefail

script_path="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"
qfw_example_parse_common_options "$@"
set -- "${QFW_EXAMPLE_REMAINING_ARGS[@]}"
original_args=("$@")

usage() {
	cat <<EOF
Usage: ./qfw_iqm_chem_smoke.sh [--verbose] [options] [chemistry-script.py] [script args...]

Run the QFw-enabled chemistry application smoke and preserve stdout/stderr in
a JSONL evidence record.

Options:
  --backend NAME              QPM provider/backend (default: iqm)
  --target-device DEVICE      QFw device id (default: QFW_QPU_DEVICE_ID or ornl-iqm-20q)
  --site-config PATH          Site config for site-mode runs
  --qpm-run-dir DIR           QPM manager run directory to verify remains alive
  --qfw-src DIR               QFw source checkout for optional build/install
  --build-dir DIR             CMake build directory for optional build/install
  --install-prefix DIR        QFw install prefix to activate
  --shared-venv DIR           Shared virtual environment passed to qfw-activate
  --build-install             Build and install QFw before running the smoke
  --chem-app-dir DIR          chemistry_example_aim2 checkout
  --service-mode MODE         local or site (default: site for iqm, local otherwise)
  --partition NAME            Slurm partition for self-allocation
  --nodes N                   Slurm node count for self-allocation (default: 1)
  --nodelist LIST             Slurm nodelist for self-allocation
  --time TIME                 Slurm walltime for self-allocation (default: 00:20:00)
  --shots N                   Chemistry reservation shots (default: 128)
  --reservation-qubits N      Reservation qubit count for hardware runs
  --reservation-walltime-s N  Reservation walltime seconds for hardware runs
  --reservation-ttl-s N       Reservation TTL seconds for hardware runs
  --estimator-precision VAL   Estimator precision argument
  --max-output-bytes N        Max stdout/stderr bytes embedded in JSON (default: 65536)
  --run-dir DIR               Evidence run directory
  --no-salloc                 Do not self-allocate when outside Slurm
  --dry-run                   Print the command and emit a skipped result
  -h, --help                  Show this help
EOF
}

backend="${QFW_CHEM_BACKEND:-iqm}"
target_device="${QFW_QPU_DEVICE_ID:-ornl-iqm-20q}"
device_access_config=""
site_config="${QFW_SITE_CONFIG:-}"
qpm_run_dir="${QFW_QPM_RUN_DIR:-}"
qfw_src="${QFW_SRC:-}"
build_dir="${QFW_BUILD:-}"
install_prefix="${QFW_PREFIX:-}"
shared_venv="${QFW_SHARED_VENV:-}"
build_install="no"
chem_app_dir="${QFW_CHEM_APP_DIR:-}"
service_mode="${QFW_CHEM_SERVICE_MODE:-}"
partition="${QFW_CHEM_SLURM_PARTITION:-}"
nodes="${QFW_CHEM_SLURM_NODES:-1}"
nodelist="${QFW_CHEM_SLURM_NODELIST:-}"
walltime="${QFW_CHEM_SLURM_TIME:-00:20:00}"
shots="${QFW_CHEM_SHOTS:-128}"
reservation_qubits="${QFW_CHEM_RESERVATION_QUBITS:-}"
reservation_walltime_s="${QFW_CHEM_RESERVATION_WALLTIME_S:-}"
reservation_ttl_s="${QFW_CHEM_RESERVATION_TTL_S:-}"
estimator_precision="${QFW_CHEM_ESTIMATOR_PRECISION:-}"
max_output_bytes="${QFW_CHEM_MAX_OUTPUT_BYTES:-65536}"
run_dir="${QFW_CHEM_RUN_DIR:-}"
self_allocate="yes"
dry_run="no"
chem_script="example_1_He_from_pyscf.py"
chem_extra_args=()

qfw_chem_need_value() {
	local option="${1:-option}"
	if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == --* ]]; then
		echo "ERROR: ${option} requires a value" >&2
		exit 2
	fi
}

while [[ $# -gt 0 ]]; do
	case "$1" in
		--backend)
			qfw_chem_need_value "$@"
			backend="$2"
			shift 2
			;;
		--target-device)
			qfw_chem_need_value "$@"
			target_device="$2"
			shift 2
			;;
		--site-config)
			qfw_chem_need_value "$@"
			site_config="$2"
			shift 2
			;;
		--qpm-run-dir)
			qfw_chem_need_value "$@"
			qpm_run_dir="$2"
			shift 2
			;;
		--qfw-src)
			qfw_chem_need_value "$@"
			qfw_src="$2"
			shift 2
			;;
		--build-dir)
			qfw_chem_need_value "$@"
			build_dir="$2"
			shift 2
			;;
		--install-prefix)
			qfw_chem_need_value "$@"
			install_prefix="$2"
			shift 2
			;;
		--shared-venv)
			qfw_chem_need_value "$@"
			shared_venv="$2"
			shift 2
			;;
		--build-install)
			build_install="yes"
			shift
			;;
		--chem-app-dir)
			qfw_chem_need_value "$@"
			chem_app_dir="$2"
			shift 2
			;;
		--service-mode)
			qfw_chem_need_value "$@"
			service_mode="$2"
			shift 2
			;;
		--partition)
			qfw_chem_need_value "$@"
			partition="$2"
			shift 2
			;;
		--nodes)
			qfw_chem_need_value "$@"
			nodes="$2"
			shift 2
			;;
		--nodelist)
			qfw_chem_need_value "$@"
			nodelist="$2"
			shift 2
			;;
		--time)
			qfw_chem_need_value "$@"
			walltime="$2"
			shift 2
			;;
		--shots)
			qfw_chem_need_value "$@"
			shots="$2"
			shift 2
			;;
		--reservation-qubits)
			qfw_chem_need_value "$@"
			reservation_qubits="$2"
			shift 2
			;;
		--reservation-walltime-s)
			qfw_chem_need_value "$@"
			reservation_walltime_s="$2"
			shift 2
			;;
		--reservation-ttl-s)
			qfw_chem_need_value "$@"
			reservation_ttl_s="$2"
			shift 2
			;;
		--estimator-precision)
			qfw_chem_need_value "$@"
			estimator_precision="$2"
			shift 2
			;;
		--max-output-bytes)
			qfw_chem_need_value "$@"
			max_output_bytes="$2"
			shift 2
			;;
		--run-dir)
			qfw_chem_need_value "$@"
			run_dir="$2"
			shift 2
			;;
		--no-salloc)
			self_allocate="no"
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
		--)
			shift
			chem_extra_args+=("$@")
			break
			;;
		--*)
			echo "ERROR: unknown option: $1" >&2
			usage >&2
			exit 2
			;;
		*)
			chem_script="$1"
			shift
			chem_extra_args+=("$@")
			break
			;;
	esac
done

qfw_chem_bool_enabled() {
	case "${1:-}" in
		1|yes|true|on|y|YES|TRUE|ON|Y) return 0 ;;
		*) return 1 ;;
	esac
}

qfw_chem_require_positive_int() {
	local name="$1"
	local value="$2"
	if ! [[ "${value}" =~ ^[0-9]+$ ]] || [[ "${value}" -le 0 ]]; then
		echo "ERROR: ${name} must be a positive integer: ${value}" >&2
		exit 2
	fi
}

qfw_chem_require_positive_int "--nodes" "${nodes}"
qfw_chem_require_positive_int "--shots" "${shots}"
qfw_chem_require_positive_int "--max-output-bytes" "${max_output_bytes}"

if [[ -z "${service_mode}" ]]; then
	if [[ "${backend}" == "iqm" ]]; then
		service_mode="site"
	else
		service_mode="local"
	fi
fi
case "${service_mode}" in
	local|site) ;;
	*) echo "ERROR: --service-mode must be local or site" >&2; exit 2 ;;
esac

if [[ -z "${run_dir}" ]]; then
	timestamp="$(date +%Y%m%d-%H%M%S)"
	run_dir="${QFW_RUN_BASE_DIR:-${TMPDIR:-/tmp}/qfw-runs}/chem-smoke-${backend}-${timestamp}"
fi
mkdir -p "${run_dir}/logs" "${run_dir}/config"

if [[ -z "${chem_app_dir}" ]]; then
	for candidate in \
		"/workspace/qfw-container-base/chemistry_example_aim2" \
		"${script_dir}/../../chemistry_example_aim2"; do
		if [[ -d "${candidate}" ]]; then
			chem_app_dir="$(cd "${candidate}" && pwd)"
			break
		fi
	done
fi

if [[ -z "${chem_app_dir}" || ! -d "${chem_app_dir}" ]]; then
	echo "ERROR: chemistry app directory not found; use --chem-app-dir" >&2
	exit 1
fi

if [[ "${self_allocate}" == "yes" &&
      -z "${SLURM_JOB_ID:-}" &&
      "${dry_run}" != "yes" &&
      -n "$(command -v salloc || true)" ]]; then
	salloc_args=(--nodes "${nodes}" --time "${walltime}")
	if [[ -n "${partition}" ]]; then
		salloc_args+=(--partition "${partition}")
	fi
	if [[ -n "${nodelist}" ]]; then
		salloc_args+=(--nodelist "${nodelist}")
	fi
	exec salloc "${salloc_args[@]}" "${script_path}" \
		--no-salloc "${original_args[@]}"
fi

qfw_chem_build_install() {
	if ! qfw_chem_bool_enabled "${build_install}"; then
		return 0
	fi
	if [[ -z "${qfw_src}" || -z "${build_dir}" || -z "${install_prefix}" ]]; then
		echo "ERROR: --build-install requires --qfw-src, --build-dir, and --install-prefix" >&2
		exit 2
	fi
	git -C "${qfw_src}" submodule update --init --recursive
	cmake -S "${qfw_src}" -B "${build_dir}" \
		-DCMAKE_BUILD_TYPE=RelWithDebInfo \
		-DCMAKE_INSTALL_PREFIX="${install_prefix}"
	cmake --build "${build_dir}" -j "$(nproc)"
	cmake --install "${build_dir}" --prefix "${install_prefix}"
}

qfw_chem_activate() {
	if [[ -z "${install_prefix}" ]]; then
		return 0
	fi
	if [[ ! -r "${install_prefix}/bin/qfw-activate" ]]; then
		echo "ERROR: qfw-activate not found under --install-prefix ${install_prefix}" >&2
		exit 1
	fi
	if [[ -n "${shared_venv}" ]]; then
		# shellcheck source=/dev/null
		source "${install_prefix}/bin/qfw-activate" --venv "${shared_venv}"
	else
		# shellcheck source=/dev/null
		source "${install_prefix}/bin/qfw-activate"
	fi
	if [[ -n "${QFW_SHARE_DIR:-}" && -d "${QFW_SHARE_DIR}/examples" ]]; then
		export QFW_EXAMPLES_DIR="${QFW_SHARE_DIR}/examples"
	fi
}

qfw_chem_qpm_ready() {
	if [[ -z "${qpm_run_dir}" ]]; then
		return 1
	fi
	qfw-qpm-svc status --run-dir "${qpm_run_dir}" >/dev/null 2>&1
}

qfw_chem_write_site_runtime_config() {
	chem_site_runtime_config="${run_dir}/config/chem-site-runtime.yaml"
	cat >"${chem_site_runtime_config}" <<EOF
resolver:
  scope-order:
    - site
EOF
}

qfw_chem_emit_result() {
	local status="$1"
	local app_rc="$2"
	local service_alive_before="$3"
	local service_alive_after="$4"
	local stdout_path="$5"
	local stderr_path="$6"
	local result_path="$7"
	local command_json="$8"
	local duration_sec="$9"
	python3 - "${status}" "${app_rc}" "${backend}" "${target_device}" \
		"${service_mode}" "${run_dir}" "${stdout_path}" "${stderr_path}" \
		"${result_path}" "${max_output_bytes}" "${qpm_run_dir}" \
		"${service_alive_before}" "${service_alive_after}" \
		"${device_access_config}" "${site_config}" "${qfw_src}" \
		"${build_dir}" "${install_prefix}" "${shared_venv}" \
		"${chem_app_dir}" "${chem_script}" "${shots}" \
		"${duration_sec}" "${command_json}" <<'PY'
import json
import os
import re
import sys
import time

(
	status,
	app_rc,
	backend,
	target_device,
	service_mode,
	run_dir,
	stdout_path,
	stderr_path,
	result_path,
	max_output_bytes,
	qpm_run_dir,
	service_alive_before,
	service_alive_after,
	device_access_config,
	site_config,
	qfw_src,
	build_dir,
	install_prefix,
	shared_venv,
	chem_app_dir,
	chem_script,
	shots,
	duration_sec,
	command_json,
) = sys.argv[1:]
max_output_bytes = int(max_output_bytes)


def read_bounded(path):
	if not path or not os.path.exists(path):
		return "", False
	with open(path, "rb") as handle:
		data = handle.read(max_output_bytes + 1)
	truncated = len(data) > max_output_bytes
	data = data[:max_output_bytes]
	return data.decode("utf-8", errors="replace"), truncated


def read_for_metadata(path, limit=4 * 1024 * 1024):
	if not path or not os.path.exists(path):
		return ""
	size = os.path.getsize(path)
	with open(path, "rb") as handle:
		if size <= limit:
			data = handle.read()
		else:
			half = limit // 2
			head = handle.read(half)
			handle.seek(max(0, size - half))
			tail = handle.read(half)
			data = head + b"\n...<metadata scan truncated>...\n" + tail
	return data.decode("utf-8", errors="replace")


stdout, stdout_truncated = read_bounded(stdout_path)
stderr, stderr_truncated = read_bounded(stderr_path)
combined = "\n".join([
	read_for_metadata(stdout_path),
	read_for_metadata(stderr_path),
])
reservation_id = None
for pattern in (
	r"reservation[_ -]?id['\"=: ]+([A-Za-z0-9_.:-]+)",
	r"reservation['\"=: ]+([A-Za-z0-9_.:-]+)",
):
	match = re.search(pattern, combined, re.IGNORECASE)
	if match:
		reservation_id = match.group(1)
		break
release_result = None
match = re.search(
	r"(QFw reservation released:[^\n]+|"
	r"release[^\n]*(ok|success|released|accepted|error|failed))",
	combined,
	re.IGNORECASE,
)
if match:
	release_result = match.group(0).strip()

example_records = []
if result_path and os.path.exists(result_path):
	with open(result_path, "r", encoding="utf-8") as handle:
		for line in handle:
			line = line.strip()
			if not line:
				continue
			try:
				example_records.append(json.loads(line))
			except json.JSONDecodeError:
				pass

record = {
	"schema": "qfw-chemistry-smoke-v1",
	"kind": "chemistry-smoke",
	"status": status,
	"rc": int(app_rc),
	"backend": backend,
	"target_device": target_device,
	"service_mode": service_mode,
	"run_dir": run_dir,
	"stdout_path": stdout_path,
	"stderr_path": stderr_path,
	"stdout": stdout,
	"stderr": stderr,
	"stdout_truncated": stdout_truncated,
	"stderr_truncated": stderr_truncated,
	"max_output_bytes": max_output_bytes,
	"result_path": result_path,
	"example_record_count": len(example_records),
	"example_records": example_records,
	"reservation_id": reservation_id,
	"release_result": release_result,
	"qpm_run_dir": qpm_run_dir,
	"service_alive_before": service_alive_before == "true",
	"service_alive_after": service_alive_after == "true",
	"device_access_config": device_access_config,
	"site_config": site_config,
	"qfw_src": qfw_src,
	"build_dir": build_dir,
	"install_prefix": install_prefix,
	"shared_venv": shared_venv,
	"virtual_env": os.environ.get("VIRTUAL_ENV"),
	"chem_app_dir": chem_app_dir,
	"chem_script": chem_script,
	"shots": int(shots),
	"slurm_job_id": os.environ.get("SLURM_JOB_ID"),
	"slurm_job_nodelist": os.environ.get("SLURM_JOB_NODELIST"),
	"command": json.loads(command_json),
	"timestamp_ns": time.time_ns(),
	"duration_sec": float(duration_sec),
}
line = "QFW_CHEM_SMOKE_RESULT " + json.dumps(
	record, indent=2, sort_keys=True)
print(line)
summary_path = os.path.join(run_dir, "chemistry-smoke.jsonl")
with open(summary_path, "a", encoding="utf-8") as handle:
	handle.write(json.dumps(record, sort_keys=True) + "\n")
PY
}

qfw_chem_build_install
qfw_chem_activate
if [[ -z "${shared_venv}" && -n "${VIRTUAL_ENV:-}" ]]; then
	shared_venv="${VIRTUAL_ENV}"
fi
if ! qfw_chem_bool_enabled "${dry_run}"; then
	qfw_example_require_runtime
fi

qfw_chem_write_site_runtime_config

if [[ "${service_mode}" == "site" && -z "${site_config}" ]]; then
	echo "ERROR: --site-config is required for site mode" >&2
	exit 2
fi

stdout_path="${run_dir}/logs/chemistry.stdout.log"
stderr_path="${run_dir}/logs/chemistry.stderr.log"
example_result_path="${run_dir}/chemistry-example.jsonl"
service_alive_before="unknown"
service_alive_after="unknown"
if [[ -n "${qpm_run_dir}" ]]; then
	if qfw_chem_qpm_ready; then
		service_alive_before="true"
	else
		service_alive_before="false"
	fi
fi

chem_command=(
	"$(qfw_example_path qfw_chem_app.sh)"
	--service-mode "${service_mode}"
	--backend "${backend}"
	--chem-app-dir "${chem_app_dir}"
	"${chem_script}"
	--smoke
	--no-draw
	--reservation-shots "${shots}"
)
if [[ "${service_mode}" == "site" ]]; then
	chem_command=(
		"$(qfw_example_path qfw_chem_app.sh)"
		--service-mode "${service_mode}"
		--backend "${backend}"
		--site-config "${site_config}"
		--runtime-config "${chem_site_runtime_config}"
		--chem-app-dir "${chem_app_dir}"
		"${chem_script}"
		--smoke
		--no-draw
		--reservation-shots "${shots}")
fi
if [[ -n "${reservation_qubits}" ]]; then
	chem_command+=(--reservation-qubits "${reservation_qubits}")
fi
if [[ -n "${reservation_walltime_s}" ]]; then
	chem_command+=(--reservation-walltime-s "${reservation_walltime_s}")
fi
if [[ -n "${reservation_ttl_s}" ]]; then
	chem_command+=(--reservation-ttl-s "${reservation_ttl_s}")
fi
if [[ -n "${estimator_precision}" ]]; then
	chem_command+=(--estimator-precision "${estimator_precision}")
fi
chem_command+=("${chem_extra_args[@]}")

command_json="$(python3 - "${chem_command[@]}" <<'PY'
import json
import sys

print(json.dumps(sys.argv[1:]))
PY
)"

start_epoch="$(date +%s)"
app_rc=0
if qfw_chem_bool_enabled "${dry_run}"; then
	printf "DRY RUN: " >"${stdout_path}"
	printf "%q " "${chem_command[@]}" >>"${stdout_path}"
	printf "\n" >>"${stdout_path}"
	app_rc=0
	status="skipped"
else
	set +e
	(
		export QFW_EXAMPLE_RESULT_FILE="${example_result_path}"
		export QFW_RUN_BASE_DIR="${run_dir}/qfw-runs"
		export QFW_CHEM_SERVICE_MODE="${service_mode}"
		export QFW_CHEM_BACKEND="${backend}"
		export QFW_CHEM_APP_DIR="${chem_app_dir}"
		export QFW_QPU_DEVICE_ID="${target_device}"
		if [[ "${service_mode}" == "site" ]]; then
			export QFW_SITE_CONFIG="${site_config}"
			export QFW_RUNTIME_CONFIG="${chem_site_runtime_config}"
		fi
		"${chem_command[@]}"
	) >"${stdout_path}" 2>"${stderr_path}"
	app_rc=$?
	set -e
	status="ok"
	if [[ "${app_rc}" -ne 0 ]]; then
		status="error"
	fi
fi

if [[ -n "${qpm_run_dir}" ]]; then
	if qfw_chem_qpm_ready; then
		service_alive_after="true"
	else
		service_alive_after="false"
		if [[ "${service_mode}" == "site" && "${status}" == "ok" ]]; then
			status="error"
			app_rc=1
		fi
	fi
fi

duration_sec="$(( $(date +%s) - start_epoch ))"
qfw_chem_emit_result "${status}" "${app_rc}" "${service_alive_before}" \
	"${service_alive_after}" "${stdout_path}" "${stderr_path}" \
	"${example_result_path}" "${command_json}" "${duration_sec}"

echo "Chemistry smoke evidence: ${run_dir}/chemistry-smoke.jsonl"
exit "${app_rc}"
