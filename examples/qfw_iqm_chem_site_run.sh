#!/bin/bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/qfw_example_common.sh"

usage() {
	cat <<EOF
Usage: ./qfw_iqm_chem_site_run.sh [options] [chemistry-script args...]

Start a site-style DEFw directory service and ORNL IQM QPM, reserve through the
QFw Slurm-style driver, run the QFw-enabled chemistry application, and then
tear the site services down.

Options:
  --base DIR                 Shared Docker/workflow base directory
  --venv DIR                 Shared Python virtual environment
  --qfw-prefix DIR           Installed QFw prefix
  --backend NAME             QPM provider/backend (default: iqm)
  --target-device DEVICE     QFw device id (default: ornl-iqm-20q)
  --owner USER               Trusted launcher user (default: root)
  --shots N                  Reservation and estimator shots (default: 1000)
  --estimator-precision VAL  Estimator precision (default maps to 1000 shots)
  --reservation-qubits N     Reservation qubit count (default: 5)
  --reservation-count N      Reservation task count (default: 1)
  --reservation-walltime-s N Reservation walltime seconds (default: 120)
  --reservation-ttl-s N      Reservation TTL seconds (default: 300)
  --chem-app-dir DIR         chemistry_example_aim2 checkout
  --chem-script SCRIPT       Chemistry script name/path
  --run-dir DIR              Run directory
  --keep-services            Leave site services running after the app
  -h, --help                 Show this help

Environment overrides use the same names without leading dashes, upper-cased
and prefixed with QFW_, for example QFW_SHOTS and QFW_CHEM_APP_DIR.
EOF
}

need_value() {
	local option="${1:-option}"
	if [[ $# -lt 2 || -z "${2:-}" || "${2:-}" == --* ]]; then
		echo "ERROR: ${option} requires a value" >&2
		exit 2
	fi
}

bool_enabled() {
	case "${1:-}" in
		1|yes|true|on|y|YES|TRUE|ON|Y) return 0 ;;
		*) return 1 ;;
	esac
}

positive_int() {
	local name="$1"
	local value="$2"
	if ! [[ "${value}" =~ ^[0-9]+$ ]] || [[ "${value}" -le 0 ]]; then
		echo "ERROR: ${name} must be a positive integer: ${value}" >&2
		exit 2
	fi
}

default_prefix_from_examples() {
	local candidate
	candidate="$(cd "${script_dir}/../../.." 2>/dev/null && pwd || true)"
	if [[ -n "${candidate}" && -r "${candidate}/bin/qfw-activate" ]]; then
		printf "%s\n" "${candidate}"
		return 0
	fi
	return 1
}

default_chem_app_dir() {
	local candidate
	for candidate in \
		"${CHEM_APP_DIR:-}" \
		"${QFW_CHEM_APP_DIR:-}" \
		"${BASE:-}/chemistry_example_aim2" \
		"${script_dir}/../../chemistry_example_aim2" \
		"$(qfw_example_path tests/chemistry_example_aim2)"; do
		if [[ -n "${candidate}" && -d "${candidate}" ]]; then
			(cd "${candidate}" && pwd)
			return 0
		fi
	done
	return 1
}

BASE="${QFW_BASE:-/workspace/qfw-container-base}"
VENV="${QFW_VENV:-${QFW_SHARED_VENV:-${BASE}/qfw-shared-test-venv}}"
QFW_PREFIX="${QFW_PREFIX:-}"
DEVICE="${QFW_DEVICE:-${QFW_QPU_DEVICE_ID:-ornl-iqm-20q}}"
BACKEND="${QFW_BACKEND:-${QFW_CHEM_BACKEND:-iqm}}"
OWNER="${QFW_OWNER:-${QFW_CHEM_OWNER:-root}}"
SHOTS="${QFW_SHOTS:-${QFW_CHEM_SHOTS:-1000}}"
RESERVATION_QUBITS="${QFW_RESERVATION_QUBITS:-${QFW_CHEM_RESERVATION_QUBITS:-5}}"
RESERVATION_COUNT="${QFW_RESERVATION_COUNT:-${QFW_CHEM_RESERVATION_COUNT:-1}}"
RESERVATION_WALLTIME_S="${QFW_RESERVATION_WALLTIME_S:-${QFW_CHEM_RESERVATION_WALLTIME_S:-120}}"
RESERVATION_TTL_S="${QFW_RESERVATION_TTL_S:-${QFW_CHEM_RESERVATION_TTL_S:-300}}"
ESTIMATOR_PRECISION="${QFW_ESTIMATOR_PRECISION:-${QFW_CHEM_ESTIMATOR_PRECISION:-0.031623}}"
CHEM_SCRIPT="${QFW_CHEM_SCRIPT:-example_1_He_from_pyscf.py}"
CHEM_APP_DIR="${QFW_CHEM_APP_DIR:-}"
RUN_ROOT="${QFW_RUN_ROOT:-}"
KEEP_SERVICES="${QFW_KEEP_SERVICES:-0}"
chem_args=()

while [[ $# -gt 0 ]]; do
	case "$1" in
		--base)
			need_value "$@"
			BASE="$2"
			shift 2
			;;
		--venv)
			need_value "$@"
			VENV="$2"
			shift 2
			;;
		--qfw-prefix)
			need_value "$@"
			QFW_PREFIX="$2"
			shift 2
			;;
		--backend)
			need_value "$@"
			BACKEND="$2"
			shift 2
			;;
		--target-device)
			need_value "$@"
			DEVICE="$2"
			shift 2
			;;
		--owner)
			need_value "$@"
			OWNER="$2"
			shift 2
			;;
		--shots)
			need_value "$@"
			SHOTS="$2"
			shift 2
			;;
		--estimator-precision)
			need_value "$@"
			ESTIMATOR_PRECISION="$2"
			shift 2
			;;
		--reservation-qubits)
			need_value "$@"
			RESERVATION_QUBITS="$2"
			shift 2
			;;
		--reservation-count)
			need_value "$@"
			RESERVATION_COUNT="$2"
			shift 2
			;;
		--reservation-walltime-s)
			need_value "$@"
			RESERVATION_WALLTIME_S="$2"
			shift 2
			;;
		--reservation-ttl-s)
			need_value "$@"
			RESERVATION_TTL_S="$2"
			shift 2
			;;
		--chem-app-dir)
			need_value "$@"
			CHEM_APP_DIR="$2"
			shift 2
			;;
		--chem-script)
			need_value "$@"
			CHEM_SCRIPT="$2"
			shift 2
			;;
		--run-dir)
			need_value "$@"
			RUN_ROOT="$2"
			shift 2
			;;
		--keep-services)
			KEEP_SERVICES=1
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		--)
			shift
			chem_args+=("$@")
			break
			;;
		*)
			chem_args+=("$1")
			shift
			;;
	esac
done

positive_int "--shots" "${SHOTS}"
positive_int "--reservation-qubits" "${RESERVATION_QUBITS}"
positive_int "--reservation-count" "${RESERVATION_COUNT}"
positive_int "--reservation-walltime-s" "${RESERVATION_WALLTIME_S}"
positive_int "--reservation-ttl-s" "${RESERVATION_TTL_S}"

if [[ -z "${QFW_PREFIX}" && -d "${BASE}/qfw-install-dev" ]]; then
	QFW_PREFIX="${BASE}/qfw-install-dev"
elif [[ -z "${QFW_PREFIX}" ]]; then
	QFW_PREFIX="$(default_prefix_from_examples || true)"
fi

if [[ -z "${RUN_ROOT}" ]]; then
	if [[ -d "${BASE}" ]]; then
		RUN_ROOT="${BASE}/qfw-runs/chem-iqm-site-$(date +%Y%m%d-%H%M%S)"
	else
		RUN_ROOT="${TMPDIR:-/tmp}/qfw-runs/chem-iqm-site-$(date +%Y%m%d-%H%M%S)"
	fi
fi

if [[ -d "${VENV}" ]]; then
	# shellcheck source=/dev/null
	source "${VENV}/bin/activate"
fi
if [[ -n "${QFW_PREFIX}" && -r "${QFW_PREFIX}/bin/qfw-activate" ]]; then
	if [[ -d "${VENV}" ]]; then
		# shellcheck source=/dev/null
		source "${QFW_PREFIX}/bin/qfw-activate" --venv "${VENV}"
	else
		# shellcheck source=/dev/null
		source "${QFW_PREFIX}/bin/qfw-activate"
	fi
fi

qfw_example_require_runtime

CHEM_APP_DIR="$(default_chem_app_dir)" || {
	echo "ERROR: chemistry app directory not found; use --chem-app-dir" >&2
	exit 2
}

mkdir -p "${RUN_ROOT}/logs"

echo "QFW_CHEM_RUN_ROOT=${RUN_ROOT}"
echo "QFW_CHEM_BACKEND=${BACKEND}"
echo "QFW_CHEM_TARGET_DEVICE=${DEVICE}"
echo "QFW_CHEM_SHOTS=${SHOTS}"
echo "QFW_CHEM_ESTIMATOR_PRECISION=${ESTIMATOR_PRECISION}"

export QFW_QPU_DEVICE_ID="${DEVICE}"

service_started=0
cleanup() {
	local rc=$?

	if [[ "${service_started}" == "1" ]] &&
	   ! bool_enabled "${KEEP_SERVICES}"; then
		set +e
		"$(qfw_example_path qfw_iqm_site_services.sh)" stop \
			--run-dir "${RUN_ROOT}/services" \
			>"${RUN_ROOT}/logs/site-stop.stdout.log" \
			2>"${RUN_ROOT}/logs/site-stop.stderr.log"
		local stop_rc=$?
		echo "QFW_CHEM_SITE_STOP_RC=${stop_rc}"
		set -e
	elif [[ "${service_started}" == "1" ]]; then
		echo "QFW_CHEM_SERVICES_LEFT_RUNNING=${RUN_ROOT}/services"
	fi

	echo "QFW_CHEM_RUN_ROOT=${RUN_ROOT}"
	echo "QFW_CHEM_SITE_START_STDOUT=${RUN_ROOT}/logs/site-start.stdout.log"
	echo "QFW_CHEM_SITE_START_STDERR=${RUN_ROOT}/logs/site-start.stderr.log"
	echo "QFW_CHEM_DRIVER_STDOUT=${RUN_ROOT}/logs/chem-driver.stdout.log"
	echo "QFW_CHEM_DRIVER_STDERR=${RUN_ROOT}/logs/chem-driver.stderr.log"
	echo "QFW_CHEM_DRIVER_RESULT=${RUN_ROOT}/chem-driver/driver.jsonl"
	echo "QFW_CHEM_SERVICE_QPM_LOG=${RUN_ROOT}/services/services/iqm-ornl-20q/logs/defw_py.log"
	exit "${rc}"
}
trap cleanup EXIT

	"$(qfw_example_path qfw_iqm_site_services.sh)" start \
	--target-device "${DEVICE}" \
	--run-dir "${RUN_ROOT}/services" \
	>"${RUN_ROOT}/logs/site-start.stdout.log" \
	2>"${RUN_ROOT}/logs/site-start.stderr.log"
service_started=1
echo "QFW_CHEM_SITE_START_RC=0"

"$(qfw_example_path qfw_iqm_chem_driver.sh)" \
	--backend "${BACKEND}" \
	--target-device "${DEVICE}" \
	--service-run-dir "${RUN_ROOT}/services" \
	--chem-app-dir "${CHEM_APP_DIR}" \
	--run-dir "${RUN_ROOT}/chem-driver" \
	--owner "${OWNER}" \
	--shots "${SHOTS}" \
	--reservation-qubits "${RESERVATION_QUBITS}" \
	--reservation-count "${RESERVATION_COUNT}" \
	--reservation-walltime-s "${RESERVATION_WALLTIME_S}" \
	--reservation-ttl-s "${RESERVATION_TTL_S}" \
	--estimator-precision "${ESTIMATOR_PRECISION}" \
	"${CHEM_SCRIPT}" \
	--smoke \
	--no-draw \
	"${chem_args[@]}" \
	>"${RUN_ROOT}/logs/chem-driver.stdout.log" \
	2>"${RUN_ROOT}/logs/chem-driver.stderr.log"
echo "QFW_CHEM_DRIVER_RC=0"
